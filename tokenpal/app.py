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
from tokenpal.config.loader import load_config
from tokenpal.config.schema import TokenPalConfig
from tokenpal.llm.registry import discover_backends, resolve_backend
from tokenpal.senses.base import AbstractSense
from tokenpal.senses.registry import discover_senses, resolve_senses
from tokenpal.ui.ascii_renderer import SpeechBubble
from tokenpal.ui.registry import discover_overlays, resolve_overlay
from tokenpal.util.logging import setup_logging

log = logging.getLogger(__name__)

_DATA_DIR = Path.home() / ".tokenpal"


def main() -> None:
    args = parse_args()

    if args.version:
        print_version()
        return

    setup_logging(verbose=args.verbose)

    if args.check:
        sys.exit(run_check(config_path=args.config))

    config = load_config(config_path=args.config)
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
            voice = load_profile(config.brain.active_voice, _DATA_DIR / "voices")
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
            db_path=_DATA_DIR / "memory.db",
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
