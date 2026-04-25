"""Write [audio] toggles into config.toml.

Used by the options modal (Textual + Qt) when the user flips a voice or
ambient toggle. Phase 1 only persists the two opt-in flags — backend
choice, voice ID, and other [audio] fields stay schema-default until
phase 2 lands the Kokoro backend that actually consumes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tokenpal.config.toml_writer import update_config


def set_voice_conversation_enabled(enabled: bool) -> Path:
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("audio", {})["voice_conversation_enabled"] = enabled

    return update_config(mutate)


def set_speak_ambient_enabled(enabled: bool) -> Path:
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("audio", {})["speak_ambient_enabled"] = enabled

    return update_config(mutate)
