"""TTS speak facade — output-only routing. Synthesis lands in phase 2."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tokenpal.audio.pipeline import AudioPipeline

log = logging.getLogger(__name__)

Source = Literal["typed", "voice", "ambient"]


async def speak(
    text: str,
    *,
    source: Source,
    pipeline: AudioPipeline,
) -> None:
    """Speak ``text`` if the routing rules permit it."""
    cfg = pipeline.config
    if source == "typed":
        return
    if source == "ambient" and not cfg.speak_ambient_enabled:
        return
    if source == "voice" and not cfg.voice_conversation_enabled:
        return
    log.debug("tts.speak source=%s text=%r", source, text[:60])
