"""Write [audio] toggles into config.toml.

Used by the options modal (Textual + Qt) and the /voice-io slash
command when the user flips a voice or ambient toggle.
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
