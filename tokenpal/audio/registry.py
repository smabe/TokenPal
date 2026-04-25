"""Decorator + walk-packages discovery for audio backends.

Mirrors tokenpal/senses/registry.py and tokenpal/llm/registry.py. Output-side
backends (TTS) and input-side backends (ASR / wake) are kept in separate
registries so ``discover_backends(include_input=False)`` skips importing
input-side modules — that's what keeps ambient-only boots from pulling
sounddevice / openwakeword / faster_whisper.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable

from tokenpal.audio.base import ASRBackend, TTSBackend, WakeWordBackend

log = logging.getLogger(__name__)


class _BackendRegistry[B]:
    """Generic name→class map used by all three audio-backend kinds."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._backends: dict[str, type[B]] = {}

    def register(self, name: str) -> Callable[[type[B]], type[B]]:
        def decorator(cls: type[B]) -> type[B]:
            if name in self._backends:
                log.debug("re-registering %s backend %r", self._kind, name)
            self._backends[name] = cls
            return cls
        return decorator

    def get(self, name: str) -> type[B]:
        if name not in self._backends:
            raise KeyError(
                f"unknown {self._kind} backend {name!r} — "
                f"registered: {sorted(self._backends)}",
            )
        return self._backends[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))


_TTS = _BackendRegistry[TTSBackend]("TTS")
_WAKE = _BackendRegistry[WakeWordBackend]("wakeword")
_ASR = _BackendRegistry[ASRBackend]("ASR")

# Public surface — preserved verbatim for back-compat with stage 2-4 tests.
register_tts_backend = _TTS.register
get_tts_backend = _TTS.get
registered_tts_backends = _TTS.names

register_wakeword_backend = _WAKE.register
get_wakeword_backend = _WAKE.get
registered_wakeword_backends = _WAKE.names

register_asr_backend = _ASR.register
get_asr_backend = _ASR.get
registered_asr_backends = _ASR.names


def discover_backends(*, include_input: bool = False) -> None:
    """Import each backend module so its decorator runs.

    ``include_input`` gates input-side modules. Output-only callers
    (ambient narration) leave it False; voice-conversation boots set it
    True.
    """
    import tokenpal.audio.backends as pkg

    for info in pkgutil.iter_modules(pkg.__path__, prefix=f"{pkg.__name__}."):
        is_input = info.name.rsplit(".", 1)[-1].startswith(("asr_", "wake_"))
        if is_input and not include_input:
            continue
        try:
            importlib.import_module(info.name)
        except ImportError as e:
            log.warning("audio backend %s skipped: %s", info.name, e)
