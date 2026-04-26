"""Write [audio] toggles into config.toml.

Used by the options modal (Textual + Qt) and the /voice-io slash command
when the user flips a voice or ambient toggle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_audio_field(field_name: str, enabled: bool) -> Path:
    """Generic setter for any [audio] boolean toggle.

    Mirrors tokenpal.config.senses_writer.set_sense_enabled. The named
    wrappers below are thin aliases retained for tests / explicit callers.
    """
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("audio", {})[field_name] = enabled

    return update_config(mutate)


def set_voice_conversation_enabled(enabled: bool) -> Path:
    return set_audio_field("voice_conversation_enabled", enabled)


def set_speak_ambient_enabled(enabled: bool) -> Path:
    return set_audio_field("speak_ambient_enabled", enabled)


def set_speak_typed_replies_enabled(enabled: bool) -> Path:
    return set_audio_field("speak_typed_replies_enabled", enabled)
