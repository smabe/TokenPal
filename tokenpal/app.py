"""TokenPal application bootstrap — wires discovery, resolution, and runtime."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import signal
import sys
import threading
from pathlib import Path

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.registry import discover_actions, resolve_actions
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.cli import parse_args, print_version, run_check
from tokenpal.commands import CommandDispatcher, CommandResult
from tokenpal.config.loader import load_config
from tokenpal.config.schema import TokenPalConfig
from tokenpal.llm.registry import discover_backends, resolve_backend
from tokenpal.senses.base import AbstractSense
from tokenpal.senses.registry import discover_senses, resolve_senses
from tokenpal.ui.ascii_renderer import SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import discover_overlays, resolve_overlay
from tokenpal.util.logging import setup_logging

log = logging.getLogger(__name__)


def main() -> None:
    args = parse_args()

    if args.version:
        print_version()
        return

    # Load config early so data_dir is available for logging
    config = load_config(config_path=args.config)
    data_dir = Path(config.paths.data_dir).expanduser().resolve()

    setup_logging(verbose=args.verbose, log_dir=data_dir / "logs")

    if args.check:
        sys.exit(run_check(config_path=args.config))

    log.info("TokenPal starting up...")

    # Discover all plugins
    discover_senses(extra_packages=config.plugins.extra_packages)
    discover_backends()
    discover_overlays()
    discover_actions()

    # Resolve implementations for this platform + config
    sense_flags = {f.name: getattr(config.senses, f.name) for f in dataclasses.fields(config.senses)}
    senses = resolve_senses(
        sense_flags=sense_flags,
        sense_overrides=config.plugins.sense_overrides,
    )

    llm_config = dataclasses.asdict(config.llm)
    llm = resolve_backend(llm_config)

    ui_config = dataclasses.asdict(config.ui)
    overlay = resolve_overlay(ui_config)

    # Load voice profile if configured
    voice = None
    if config.brain.active_voice:
        from tokenpal.tools.voice_profile import load_profile

        try:
            voice = load_profile(config.brain.active_voice, data_dir / "voices")
            log.info("Loaded voice '%s' (%d lines)", voice.character, len(voice.lines))
        except FileNotFoundError:
            log.warning("Voice '%s' not found — using defaults", config.brain.active_voice)

    personality = PersonalityEngine(config.brain.persona_prompt, voice=voice)

    # Actions (LLM-callable tools)
    actions = []
    if config.actions.enabled:
        action_flags = {
            f.name: getattr(config.actions, f.name)
            for f in dataclasses.fields(config.actions)
            if f.name != "enabled"
        }
        actions = resolve_actions(enabled=action_flags)
        if actions:
            log.info("Loaded %d actions: %s", len(actions), [a.action_name for a in actions])

    # Session memory
    memory: MemoryStore | None = None
    if config.memory.enabled:
        memory = MemoryStore(
            db_path=data_dir / "memory.db",
            retention_days=config.memory.retention_days,
        )
        memory.setup()
        memory.record_session_start()

    # Startup summary (before overlay takes over terminal)
    _print_startup_summary(senses, actions, config)

    # Set up the overlay (must happen on main thread)
    overlay.setup()

    # Build the brain
    brain = Brain(
        senses=senses,
        llm=llm,
        ui_callback=lambda text: overlay.schedule_callback(
            lambda: overlay.show_speech(SpeechBubble(text=text))
        ),
        personality=personality,
        status_callback=lambda text: overlay.schedule_callback(
            lambda t=text: overlay.update_status(t)
        ),
        memory=memory,
        actions=actions,
        poll_interval_s=config.brain.poll_interval_s,
        comment_cooldown_s=config.brain.comment_cooldown_s,
        interestingness_threshold=config.brain.interestingness_threshold,
        context_max_tokens=config.brain.context_max_tokens,
        sense_intervals=config.brain.sense_intervals,
    )

    # Slash command dispatcher
    dispatcher = CommandDispatcher()

    def _cmd_help(_args: str) -> CommandResult:
        return CommandResult(dispatcher.help_text())

    def _cmd_clear(_args: str) -> CommandResult:
        overlay.schedule_callback(overlay.hide_speech)
        return CommandResult("")

    def _cmd_mood(_args: str) -> CommandResult:
        return CommandResult(f"Mood: {personality.mood}")

    def _cmd_status(_args: str) -> CommandResult:
        sense_names = ", ".join(s.sense_name for s in senses)
        action_names = ", ".join(a.action_name for a in actions)
        return CommandResult(
            f"Model: {llm.model_name} | "
            f"Senses: {sense_names} | "
            f"Actions: {action_names or 'none'}"
        )

    def _cmd_model(args: str) -> CommandResult:
        name = args.strip()
        if not name:
            return CommandResult(f"Current model: {llm.model_name}")
        llm.set_model(name)
        return CommandResult(f"Switched to {name}")

    def _cmd_voice(args: str) -> CommandResult:
        return _handle_voice_command(
            args, personality, data_dir / "voices", overlay
        )

    dispatcher.register("help", _cmd_help)
    dispatcher.register("clear", _cmd_clear)
    dispatcher.register("mood", _cmd_mood)
    dispatcher.register("status", _cmd_status)
    dispatcher.register("model", _cmd_model)
    dispatcher.register("voice", _cmd_voice)

    # Wire input callbacks
    def _on_command(raw_input: str) -> None:
        result = dispatcher.dispatch(raw_input)
        if result.message:
            overlay.schedule_callback(
                lambda: overlay.show_speech(SpeechBubble(text=result.message))
            )

    def _on_user_input(text: str) -> None:
        brain.submit_user_input(text)

    overlay.set_command_callback(_on_command)
    overlay.set_input_callback(_on_user_input)

    # Brain runs in a background thread with its own asyncio loop
    brain_thread = threading.Thread(
        target=lambda: asyncio.run(brain.start()),
        daemon=True,
        name="tokenpal-brain",
    )
    brain_thread.start()
    log.info("Brain thread started")

    # Handle Ctrl+C gracefully
    def _shutdown(*args: object) -> None:
        log.info("Shutting down...")
        overlay.schedule_callback(overlay.teardown)

    signal.signal(signal.SIGINT, _shutdown)

    # Block on the UI event loop (main thread)
    try:
        overlay.run_loop()
    finally:
        asyncio.run(brain.stop())
        if memory:
            memory.teardown()
        _unload_model(llm.model_name)
        log.info("TokenPal shut down cleanly")


def _print_startup_summary(
    senses: list[AbstractSense],
    actions: list[AbstractAction],
    config: TokenPalConfig,
) -> None:
    """Print a brief status summary to stderr before the overlay takes over."""
    sense_names = [s.sense_name for s in senses]
    action_names = [a.action_name for a in actions]
    model = config.llm.model_name

    lines = [
        f"  model: {model}",
        f"  senses: {', '.join(sense_names) or 'none'}",
    ]
    if action_names:
        lines.append(f"  actions: {', '.join(action_names)}")

    summary = "\n".join(lines)
    print(f"\n\033[1mTokenPal\033[0m\n{summary}\n", file=sys.stderr)


def _unload_model(model_name: str) -> None:
    """Unload the model from Ollama to free RAM."""
    import subprocess

    try:
        subprocess.run(
            ["ollama", "stop", model_name],
            capture_output=True,
            timeout=5,
        )
        log.info("Unloaded model '%s' from Ollama", model_name)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _handle_voice_command(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
) -> CommandResult:
    """Handle /voice subcommands."""
    from tokenpal.tools.voice_profile import list_profiles, load_profile

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    subargs = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "list":
        profiles = list_profiles(voices_dir)
        if not profiles:
            return CommandResult("No voices saved yet.")
        items = [f"{name} ({count} lines)" for _, name, count in profiles]
        return CommandResult("Voices: " + ", ".join(items))

    if subcmd == "info":
        name = personality.voice_name
        if not name:
            return CommandResult("Using default TokenPal voice.")
        return CommandResult(f"Voice: {name}")

    if subcmd == "off":
        personality.set_voice(None)
        return CommandResult("Back to default TokenPal.")

    if subcmd == "switch":
        if not subargs:
            return CommandResult("Usage: /voice switch <name>")
        try:
            from tokenpal.tools.voice_profile import slugify
            profile = load_profile(slugify(subargs), voices_dir)
            personality.set_voice(profile)
            return CommandResult(f"Switched to {profile.character}.")
        except FileNotFoundError:
            return CommandResult(f"Voice '{subargs}' not found.")

    if subcmd == "train":
        return _start_voice_training(
            subargs, personality, voices_dir, overlay
        )

    return CommandResult(
        "Usage: /voice list | switch <name> | off | info"
        " | train <wiki> <character>"
    )


def _start_voice_training(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
) -> CommandResult:
    """Kick off wiki voice training in a background thread."""
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return CommandResult(
            'Usage: /voice train <wiki> "<character>"'
        )

    wiki = parts[0]
    character = parts[1].strip("\"'")

    def _train() -> None:
        try:
            from tokenpal.tools.train_voice import train_from_wiki

            profile = train_from_wiki(wiki, character, voices_dir=voices_dir)
            if profile is None:
                overlay.schedule_callback(
                    lambda: overlay.show_speech(
                        SpeechBubble(text=f"Not enough lines for {character}.")
                    )
                )
                return

            personality.set_voice(profile)
            msg = f"Trained {character} ({len(profile.lines)} lines). Voice active!"
            overlay.schedule_callback(
                lambda: overlay.show_speech(SpeechBubble(text=msg))
            )
            log.info(
                "Voice trained: %s from %s (%d lines)",
                character, wiki, len(profile.lines),
            )

        except Exception:
            log.exception("Voice training failed")
            overlay.schedule_callback(
                lambda: overlay.show_speech(
                    SpeechBubble(text="Training failed. Check logs.")
                )
            )

    train_thread = threading.Thread(target=_train, daemon=True, name="voice-train")
    train_thread.start()
    return CommandResult(f"Training {character} from {wiki}...")
