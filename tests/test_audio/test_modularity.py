"""Phase-1 falsifiable modularity contract for the audio subsystem.

Promise: with voice OFF + ambient ON, booting audio and speaking an ambient
line must NOT load any of the heavy *input-side* wheels (openWakeWord,
faster-whisper, pyaudio). ``sounddevice`` itself is shared between input and
output (its OutputStream is the ambient sink), so the blocker keeps it
allowed and gates the input-stream usage at runtime when phase 3 lands.

Anti-test: with voice ON, the same blocker MUST cause boot to fail. That
proves the blocker is catching a real import, not a stub module name we
control — the blocklist targets concrete third-party deps.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pytest

INPUT_SIDE_DEPS: frozenset[str] = frozenset({
    "pyaudio",
    "openwakeword",
    "faster_whisper",
})


class _BlockInputSide:
    def find_spec(
        self, fullname: str, path: Any = None, target: Any = None,
    ) -> ModuleSpec | None:
        if fullname in INPUT_SIDE_DEPS:
            raise ImportError(
                f"input-side import {fullname!r} is forbidden in this context",
            )
        return None


@contextmanager
def _block_input_side() -> Iterator[None]:
    for name in list(sys.modules):
        if name in INPUT_SIDE_DEPS:
            del sys.modules[name]
    finder = _BlockInputSide()
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)


async def test_ambient_only_does_not_open_input(tmp_path: Path) -> None:
    from tokenpal.audio.pipeline import boot
    from tokenpal.audio.tts import speak
    from tokenpal.config.schema import AudioConfig

    cfg = AudioConfig(
        voice_conversation_enabled=False,
        speak_ambient_enabled=True,
    )
    with _block_input_side():
        pipeline = boot(cfg, tmp_path)
        # Models aren't installed in the test env — speak() bails at the
        # warmup() FileNotFoundError before ever touching sounddevice.
        await speak("hello", source="ambient", pipeline=pipeline)

        leaked = sorted(m for m in sys.modules if m in INPUT_SIDE_DEPS)
        assert not leaked, (
            f"ambient-only boot leaked input-side deps: {leaked}"
        )


async def test_voice_on_imports_input_side(tmp_path: Path) -> None:
    """Anti-test: blocker must trip when voice is on, proving it isn't a no-op."""
    from tokenpal.audio.pipeline import boot
    from tokenpal.config.schema import AudioConfig

    cfg = AudioConfig(
        voice_conversation_enabled=True,
        speak_ambient_enabled=False,
    )
    with _block_input_side():
        with pytest.raises(ImportError):
            boot(cfg, tmp_path)
