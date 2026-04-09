"""TokenPal application bootstrap — wires discovery, resolution, and runtime."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import signal
import threading

from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.config.loader import load_config
from tokenpal.llm.registry import discover_backends, resolve_backend
from tokenpal.senses.registry import discover_senses, resolve_senses
from tokenpal.ui.ascii_renderer import SpeechBubble
from tokenpal.ui.registry import discover_overlays, resolve_overlay
from tokenpal.util.logging import setup_logging

log = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    config = load_config()
    log.info("TokenPal starting up...")

    # Discover all plugins
    discover_senses(extra_packages=config.plugins.extra_packages)
    discover_backends()
    discover_overlays()

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

    personality = PersonalityEngine(config.brain.persona_prompt)

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
        log.info("TokenPal shut down cleanly")
