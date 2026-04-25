"""Decorator + walk-packages discovery for audio backends.

Mirrors tokenpal/senses/registry.py and tokenpal/llm/registry.py. Output-side
backends (TTS) and input-side backends (ASR / wake) are kept in separate
registries so ``discover_backends(include_input=False)`` skips importing any
input-side module — that's what keeps ambient-only boots from pulling
sounddevice / openwakeword / faster_whisper.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from typing import TypeVar

from tokenpal.audio.base import TTSBackend

log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=type[TTSBackend])

_TTS_BACKENDS: dict[str, type[TTSBackend]] = {}


def register_tts_backend(name: str) -> Callable[[_T], _T]:
    def decorator(cls: _T) -> _T:
        if name in _TTS_BACKENDS:
            log.debug("re-registering TTS backend %r", name)
        _TTS_BACKENDS[name] = cls
        return cls
    return decorator


def get_tts_backend(name: str) -> type[TTSBackend]:
    if name not in _TTS_BACKENDS:
        raise KeyError(
            f"unknown TTS backend {name!r} — registered: {sorted(_TTS_BACKENDS)}",
        )
    return _TTS_BACKENDS[name]


def registered_tts_backends() -> tuple[str, ...]:
    return tuple(sorted(_TTS_BACKENDS))


def discover_backends(*, include_input: bool = False) -> None:
    """Import each backend module so its decorator runs.

    ``include_input`` gates input-side modules (ASR / wake). Output-only
    callers (ambient narration) leave it False; voice-conversation boots set
    it True.
    """
    import tokenpal.audio.backends as pkg

    for info in pkgutil.iter_modules(pkg.__path__, prefix=f"{pkg.__name__}."):
        # Input-side filenames are prefixed so the gate stays mechanical.
        is_input = info.name.rsplit(".", 1)[-1].startswith(("asr_", "wake_"))
        if is_input and not include_input:
            continue
        try:
            importlib.import_module(info.name)
        except ImportError as e:
            log.warning("audio backend %s skipped: %s", info.name, e)
