"""TokenPal application bootstrap — wires discovery, resolution, and runtime."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.registry import discover_actions, resolve_actions
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.cli import parse_args, print_version, run_check
from tokenpal.commands import CommandDispatcher, CommandResult
from tokenpal.config.loader import load_config
from tokenpal.config.schema import TokenPalConfig
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.registry import discover_backends, resolve_backend
from tokenpal.senses.base import AbstractSense
from tokenpal.senses.registry import discover_senses, resolve_senses
from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
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

    # First-run welcome wizard
    if not args.skip_welcome:
        from tokenpal.first_run import needs_first_run, run_wizard
        if needs_first_run(data_dir):
            run_wizard(data_dir)
            # Reload config in case the wizard wrote weather settings
            config = load_config(config_path=args.config)
            data_dir = Path(config.paths.data_dir).expanduser().resolve()

    log.info("TokenPal starting up...")

    # Discover all plugins
    discover_senses(extra_packages=config.plugins.extra_packages)
    discover_backends()
    discover_overlays()
    discover_actions()

    # Session memory (before senses, so productivity sense can use it)
    memory: MemoryStore | None = None
    if config.memory.enabled:
        memory = MemoryStore(
            db_path=data_dir / "memory.db",
            retention_days=config.memory.retention_days,
        )
        memory.setup()
        memory.record_session_start()

    # Resolve implementations for this platform + config
    sense_flags = {f.name: getattr(config.senses, f.name) for f in dataclasses.fields(config.senses)}
    sense_configs: dict[str, dict[str, Any]] = {}
    if memory:
        sense_configs["productivity"] = {"memory_store": memory}
    sense_configs["weather"] = dataclasses.asdict(config.weather)
    senses = resolve_senses(
        sense_flags=sense_flags,
        sense_overrides=config.plugins.sense_overrides,
        sense_configs=sense_configs,
    )

    llm_config = dataclasses.asdict(config.llm)
    llm_config["server_mode"] = config.server.mode
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
        conversation=config.conversation,
    )

    # Load voice-specific buddy art into the overlay
    def _load_voice_art() -> None:
        idle, idle_alt, talking = personality.voice_frames
        if idle:
            frames = BuddyFrame.from_voice("custom", idle, idle_alt, talking)
            if hasattr(overlay, "load_voice_frames"):
                overlay.load_voice_frames(frames)
        elif hasattr(overlay, "clear_voice_frames"):
            overlay.clear_voice_frames()

    _load_voice_art()

    # Slash command dispatcher
    dispatcher = CommandDispatcher()

    def _cmd_help(_args: str) -> CommandResult:
        return CommandResult(dispatcher.help_text())

    def _cmd_clear(_args: str) -> CommandResult:
        overlay.schedule_callback(overlay.hide_speech)
        overlay.clear_log()
        brain.reset_conversation()
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
        prev_model = llm.model_name
        result = _handle_model_command(args, llm, overlay)
        if llm.model_name != prev_model:
            brain.reset_conversation()
        return result

    def _cmd_voice(args: str) -> CommandResult:
        prev_voice = personality.voice_name
        result = _handle_voice_command(
            args, personality, data_dir / "voices", overlay, brain,
            llm, config,
        )
        if personality.voice_name != prev_voice:
            brain.reset_conversation()
            _load_voice_art()
        return result

    def _cmd_server(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "status"

        if subcmd == "status":
            state = "connected" if llm.is_reachable else "unreachable"
            return CommandResult(f"Server: {llm.api_url} ({state})")

        if subcmd == "switch":
            if len(parts) < 2:
                return CommandResult("Usage: /server switch <host|local|remote>")
            target = parts[1].strip()
            if target == "local":
                url = "http://localhost:11434/v1"
            elif target == "remote":
                host = config.server.host if config.server.host != "127.0.0.1" else "localhost"
                port = config.server.port
                url = f"http://{host}:{port}/v1"
            elif not target.startswith("http"):
                url = f"http://{target}:8585/v1"
            else:
                url = target.rstrip("/")
                if not url.endswith("/v1"):
                    url += "/v1"

            llm.set_api_url(url)
            asyncio.run_coroutine_threadsafe(llm.setup(), brain._loop)
            return CommandResult(f"Switching to {url}...")

        return CommandResult("Usage: /server [status|switch <host|local|remote>]")

    def _cmd_zip(args: str) -> CommandResult:
        return _handle_zip_command(args)

    def _cmd_chatlog(_args: str) -> CommandResult:
        overlay.schedule_callback(overlay.toggle_chat_log)
        return CommandResult("")

    dispatcher.register("help", _cmd_help)
    dispatcher.register("clear", _cmd_clear)
    dispatcher.register("chatlog", _cmd_chatlog)
    dispatcher.register("mood", _cmd_mood)
    dispatcher.register("status", _cmd_status)
    dispatcher.register("model", _cmd_model)
    dispatcher.register("voice", _cmd_voice)
    dispatcher.register("server", _cmd_server)
    dispatcher.register("zip", _cmd_zip)

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
    def _run_brain() -> None:
        try:
            asyncio.run(brain.start())
        except Exception:
            log.exception("Brain thread crashed")

    brain_thread = threading.Thread(
        target=_run_brain,
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


_RECOMMENDED_MODELS: list[tuple[str, str]] = [
    ("gemma4", "Google, 9B — fast, witty, tool calling (default)"),
    ("llama3.2:3b", "Meta, 3B — very fast, lightweight"),
    ("llama3.1:8b", "Meta, 8B — balanced speed/quality"),
    ("mistral:7b", "Mistral, 7B — solid all-rounder"),
    ("phi4-mini", "Microsoft, 3.8B — compact, capable"),
    ("qwen3:8b", "Alibaba, 8B — multilingual"),
]


def _handle_zip_command(args: str) -> CommandResult:
    """Handle /zip — geocode a zip code and write weather location to config."""
    from tokenpal.config.weather import geocode_zip, write_weather_config

    zipcode = args.strip()
    if not zipcode:
        return CommandResult("Usage: /zip 90210")
    if not re.match(r"^\d{5}$", zipcode):
        return CommandResult("Enter a 5-digit US zip code, e.g. /zip 90210")

    try:
        geo = geocode_zip(zipcode)
    except Exception as e:
        return CommandResult(f"Geocoding failed: {e}")

    if not geo:
        return CommandResult(f"No location found for zip code {zipcode}")

    write_weather_config(geo.lat, geo.lon, geo.label)
    return CommandResult(f"Weather set to {geo.label} ({zipcode}). Restart TokenPal to activate.")


def _handle_model_command(
    args: str,
    llm: AbstractLLMBackend,
    overlay: AbstractOverlay,
) -> CommandResult:
    """Handle /model subcommands."""
    import json
    import urllib.request

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    subargs = parts[1].strip() if len(parts) > 1 else ""

    # No args → show current model
    if not subcmd:
        return CommandResult(f"Current model: {llm.model_name}")

    if subcmd == "list":
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            models = data.get("models", [])
            if not models:
                return CommandResult("No models installed.")
            names = []
            for m in models:
                name = m.get("name", "?")
                size_gb = m.get("size", 0) / 1e9
                marker = " *" if name == llm.model_name else ""
                names.append(f"{name} ({size_gb:.1f}GB){marker}")
            return CommandResult("Models: " + ", ".join(names))
        except Exception:
            return CommandResult("Can't reach Ollama.")

    if subcmd == "pull":
        if not subargs:
            return CommandResult("Usage: /model pull <name>")
        model = subargs

        def _pull() -> None:
            import subprocess

            bubble = SpeechBubble(
                text=f"Downloading {model}...", persistent=True,
            )
            overlay.schedule_callback(lambda: overlay.show_speech(bubble))
            overlay.schedule_callback(
                lambda: overlay.update_status(f"Pulling {model}...")
            )
            try:
                result = subprocess.run(
                    ["ollama", "pull", model],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    overlay.schedule_callback(
                        lambda: overlay.show_speech(
                            SpeechBubble(text=f"Got {model}! /model {model} to use it.")
                        )
                    )
                else:
                    err = (result.stderr or "unknown error").strip()[:60]
                    overlay.schedule_callback(
                        lambda: overlay.show_speech(
                            SpeechBubble(text=f"Pull failed: {err}")
                        )
                    )
            except Exception:
                overlay.schedule_callback(
                    lambda: overlay.show_speech(
                        SpeechBubble(text="Pull failed. Check logs.")
                    )
                )
            finally:
                overlay.schedule_callback(
                    lambda: overlay.update_status("")
                )

        threading.Thread(
            target=_pull, daemon=True, name="model-pull",
        ).start()
        return CommandResult("")

    if subcmd == "browse":
        lines = [f"{n} — {d}" for n, d in _RECOMMENDED_MODELS]
        return CommandResult(
            "Recommended: " + " | ".join(lines)
            + " — /model pull <name> to download"
        )

    # Bare name → switch model
    llm.set_model(subcmd)
    return CommandResult(f"Switched to {subcmd}")


def _handle_voice_command(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
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
        ft = " (fine-tuned)" if personality.is_finetuned else ""
        return CommandResult(f"Voice: {name}{ft}")

    if subcmd == "off":
        from tokenpal.tools.train_voice import activate_voice
        personality.set_voice(None)
        if llm and config:
            llm.set_model(config.llm.model_name)
        activate_voice("")
        return CommandResult("Back to default TokenPal.")

    if subcmd == "switch":
        from tokenpal.tools.train_voice import activate_voice
        from tokenpal.tools.voice_profile import slugify
        if not subargs:
            return CommandResult("Usage: /voice switch <name>")
        try:
            slug = slugify(subargs)
            profile = load_profile(slug, voices_dir)
            personality.set_voice(profile)
            if profile.finetuned_model and llm:
                llm.set_model(profile.finetuned_model)
            elif llm and config:
                llm.set_model(config.llm.model_name)
            activate_voice(slug)
            return CommandResult(f"Switched to {profile.character}.")
        except FileNotFoundError:
            return CommandResult(f"Voice '{subargs}' not found.")

    if subcmd == "train":
        return _start_voice_training(
            subargs, personality, voices_dir, overlay, brain
        )

    if subcmd == "finetune":
        return _start_voice_finetune(
            subargs, personality, voices_dir, overlay, brain,
            llm, config,
        )

    if subcmd == "finetune-setup":
        return _start_finetune_setup(overlay, config)

    if subcmd == "import":
        return _import_gguf(
            subargs, personality, voices_dir, overlay, llm,
        )

    if subcmd == "regenerate":
        return _start_voice_regenerate(
            subargs, personality, voices_dir, overlay,
        )

    return CommandResult(
        "Usage: /voice list | switch <name> | off | info"
        " | train <wiki> <character> | finetune <name>"
        " | finetune-setup | import <gguf_path>"
        " | regenerate [name|--all]"
    )


def _overlay_show(overlay: AbstractOverlay, msg: str, persistent: bool = False) -> None:
    """Show a speech bubble via the overlay (thread-safe)."""
    bubble = SpeechBubble(text=msg, persistent=persistent)
    overlay.schedule_callback(lambda: overlay.show_speech(bubble))


def _overlay_status(overlay: AbstractOverlay, msg: str) -> None:
    """Update the status bar via the overlay (thread-safe)."""
    overlay.schedule_callback(lambda: overlay.update_status(msg))


def _start_voice_training(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
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

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Training: {step}")

            profile = train_from_wiki(
                wiki, character, voices_dir=voices_dir,
                progress_callback=_on_progress,
            )
            if profile is None:
                _overlay_show(overlay, f"Not enough lines for {character}.")
                return

            personality.set_voice(profile)
            _overlay_show(overlay, f"I'm {character} now! ({len(profile.lines)} lines)")
            log.info(
                "Voice trained: %s from %s (%d lines)",
                character, wiki, len(profile.lines),
            )

        except Exception:
            log.exception("Voice training failed")
            _overlay_show(overlay, "Training failed. Check logs.")
        finally:
            if brain:
                brain.paused = False
            _overlay_status(overlay, "")

    _overlay_show(overlay, f"Learning to be {character}...", persistent=True)
    _overlay_status(overlay, f"Training {character}...")
    if brain:
        brain.paused = True

    train_thread = threading.Thread(target=_train, daemon=True, name="voice-train")
    train_thread.start()
    return CommandResult("")


def _start_voice_regenerate(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
) -> CommandResult:
    """Regenerate persona for existing voice profiles."""
    from tokenpal.tools.voice_profile import list_profiles, load_profile, slugify

    do_all = args.strip().lower() == "--all"
    if not args.strip():
        # Regenerate current voice
        if not personality.voice_name:
            return CommandResult(
                "No active voice. Usage: /voice regenerate <name> or --all"
            )
        slugs = [slugify(personality.voice_name)]
    elif do_all:
        profiles = list_profiles(voices_dir)
        slugs = [slug for slug, _, _ in profiles]
        if not slugs:
            return CommandResult("No voice profiles found.")
    else:
        slugs = [slugify(args.strip())]

    def _regen() -> None:
        try:
            from tokenpal.tools.train_voice import regenerate_persona

            for slug in slugs:
                try:
                    profile = load_profile(slug, voices_dir)
                except FileNotFoundError:
                    _overlay_show(overlay, f"Voice '{slug}' not found.")
                    continue

                def _on_progress(step: str) -> None:
                    _overlay_show(overlay, step, persistent=True)

                regenerate_persona(
                    profile, voices_dir, progress_callback=_on_progress,
                )

            count = len(slugs)
            msg = f"Regenerated {count} voice{'s' if count != 1 else ''}."
            _overlay_show(overlay, msg)
            log.info("Voice regeneration complete: %s", slugs)

            # Hot-swap if current voice was regenerated
            current = slugify(personality.voice_name) if personality.voice_name else ""
            if current in slugs:
                profile = load_profile(current, voices_dir)
                personality.set_voice(profile)
        except Exception:
            log.exception("Voice regeneration failed")
            _overlay_show(overlay, "Regeneration failed. Check logs.")

    label = f"{len(slugs)} voices" if do_all else slugs[0]
    _overlay_show(overlay, f"Regenerating {label}...", persistent=True)

    regen_thread = threading.Thread(
        target=_regen, daemon=True, name="voice-regen",
    )
    regen_thread.start()
    return CommandResult("")


def _start_voice_finetune(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    """Kick off remote LoRA fine-tuning in a background thread."""
    from tokenpal.tools.voice_profile import load_profile, save_profile, slugify

    if not args:
        return CommandResult("Usage: /voice finetune <voice_name>")

    slug = slugify(args)
    try:
        profile = load_profile(slug, voices_dir)
    except FileNotFoundError:
        return CommandResult(f"Voice '{args}' not found. Train it first with /voice train.")

    if not config or not config.finetune.remote.host:
        return CommandResult(
            "No remote GPU configured. Set [finetune.remote] host in config.toml"
        )

    def _finetune() -> None:
        try:
            from tokenpal.tools.remote_train import remote_finetune

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Fine-tuning: {step}")

            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                remote_finetune(profile, config.finetune, _on_progress)
            )
            loop.close()

            from datetime import datetime
            model_name = f"tokenpal-{slug}"
            profile.finetuned_model = model_name
            profile.finetuned_base = config.finetune.base_model
            profile.finetuned_date = datetime.now().isoformat(timespec="seconds")
            save_profile(profile, voices_dir)

            personality.set_voice(profile)
            if llm:
                llm.set_model(model_name)

            _overlay_show(overlay, f"{profile.character} fine-tuned! Model: {model_name}")
            log.info("Fine-tuning complete: %s → %s", slug, model_name)

        except Exception:
            log.exception("Fine-tuning failed")
            _overlay_show(overlay, "Fine-tuning failed. Check logs.")
        finally:
            if brain:
                brain.paused = False
            _overlay_status(overlay, "")

    _overlay_show(overlay, f"Fine-tuning {profile.character}...", persistent=True)
    _overlay_status(overlay, f"Fine-tuning {profile.character}...")
    if brain:
        brain.paused = True

    ft_thread = threading.Thread(target=_finetune, daemon=True, name="voice-finetune")
    ft_thread.start()
    return CommandResult("")


def _start_finetune_setup(
    overlay: AbstractOverlay,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    """Run one-time remote training environment setup."""
    if not config or not config.finetune.remote.host:
        return CommandResult(
            "No remote GPU configured. Set [finetune.remote] host in config.toml"
        )

    def _setup() -> None:
        try:
            from tokenpal.tools.remote_train import remote_setup

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Setup: {step}")

            loop = asyncio.new_event_loop()
            ok = loop.run_until_complete(
                remote_setup(config.finetune.remote, _on_progress)
            )
            loop.close()

            if ok:
                _overlay_show(overlay, "Remote training environment ready!")
            else:
                _overlay_show(overlay, "Setup failed. Check tokenpal.log for details.")
        except Exception:
            log.exception("Finetune setup failed")
            _overlay_show(overlay, "Setup failed. Check logs.")
        finally:
            _overlay_status(overlay, "")

    _overlay_show(overlay, "Setting up remote training environment...", persistent=True)
    _overlay_status(overlay, "Setting up remote training...")

    setup_thread = threading.Thread(target=_setup, daemon=True, name="finetune-setup")
    setup_thread.start()
    return CommandResult("")


def _import_gguf(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    llm: AbstractLLMBackend | None = None,
) -> CommandResult:
    """Import a GGUF file trained on another machine."""
    from tokenpal.tools.voice_profile import load_profile, save_profile

    if not args:
        return CommandResult("Usage: /voice import <gguf_path>")

    gguf_path = Path(args).expanduser().resolve()
    if not gguf_path.exists():
        return CommandResult(f"File not found: {gguf_path}")
    if not gguf_path.suffix == ".gguf":
        return CommandResult("Expected a .gguf file.")

    # Derive voice name from GGUF filename (e.g., mordecai.gguf → mordecai)
    slug = gguf_path.stem
    model_name = f"tokenpal-{slug}"

    # Try to find matching voice profile
    try:
        profile = load_profile(slug, voices_dir)
    except FileNotFoundError:
        return CommandResult(
            f"No voice profile for '{slug}'. "
            f"Train the voice first with /voice train."
        )

    # Register with Ollama
    from tokenpal.tools.dataset_prep import build_system_prompt
    from tokenpal.tools.finetune_voice import register_ollama

    system_prompt = build_system_prompt(profile)
    if not register_ollama(gguf_path, model_name, system_prompt):
        return CommandResult("Failed to register with Ollama. Is it running?")

    # Update profile
    from datetime import datetime
    profile.finetuned_model = model_name
    profile.finetuned_base = "imported"
    profile.finetuned_date = datetime.now().isoformat(timespec="seconds")
    save_profile(profile, voices_dir)

    # Activate
    personality.set_voice(profile)
    if llm:
        llm.set_model(model_name)

    return CommandResult(f"Imported and activated: {model_name}")
