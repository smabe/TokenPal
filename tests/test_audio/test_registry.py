"""Registry contract: input-side backends only load when explicitly asked.

The walk-packages gate is what keeps ambient-only boots from pulling
faster_whisper / openwakeword. The modularity test covers the runtime path;
this test pins the registry behavior at the API surface.
"""

from __future__ import annotations

from tokenpal.audio import base, registry


def test_register_and_lookup() -> None:
    @registry.register_tts_backend("dummy")
    class _Dummy(base.TTSBackend):
        sample_rate = 24000

        def list_voices(self) -> list[base.VoiceInfo]:
            return [base.VoiceInfo(id="dummy:x", raw="x", backend="dummy")]

        async def synthesize(self, text, voice_id, *, speed=1.0):
            yield b""
            return

    assert registry.get_tts_backend("dummy") is _Dummy
    assert "dummy" in registry.registered_tts_backends()


def test_unknown_backend_raises() -> None:
    import pytest
    with pytest.raises(KeyError):
        registry.get_tts_backend("does-not-exist")


def test_discover_runs_without_input_side() -> None:
    # Smoke: walk the (empty in phase 2) backends package without crashing.
    # Real gate coverage lands when phase 3 adds asr_/wake_ modules.
    registry.discover_backends(include_input=False)
    registry.discover_backends(include_input=True)
