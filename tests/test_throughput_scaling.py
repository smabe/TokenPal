"""Tests for throughput-aware max_tokens scaling (plans/gpu-scaling.md)."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.llm.http_backend import HttpBackend


def _backend(
    *,
    target_latency_scaling: bool = True,
    max_tokens: int = 60,
    per_server_max_tokens: dict[str, int] | None = None,
) -> HttpBackend:
    config: dict[str, Any] = {
        "api_url": "http://localhost:11434/v1",
        "model_name": "gemma4",
        "max_tokens": max_tokens,
        "target_latency_scaling": target_latency_scaling,
        "per_server_max_tokens": per_server_max_tokens or {},
    }
    return HttpBackend(config)


def _seed_estimator(
    b: HttpBackend, *, decode_tps: float = 50.0, ttft_s: float = 1.0
) -> None:
    """Directly populate the EWMAs. Use when the test cares about resolution,
    not about the EWMA accumulation path."""
    b._decode_tps_ewma = decode_tps
    b._ttft_ewma_s = ttft_s
    b._sample_count = b._MIN_SAMPLES_FOR_ESTIMATE


def test_first_calls_use_static_default_before_three_samples() -> None:
    b = _backend(max_tokens=60)
    # One sample only — estimator not ready.
    b._record_sample(completion_tokens=100, total_elapsed_s=2.0)
    assert not b._estimate_ready
    assert b._resolve_max_tokens(None, 5.0, 40) == 60


def test_three_samples_populate_ewmas_and_enable_rule3() -> None:
    b = _backend(max_tokens=60)
    _seed_estimator(b, decode_tps=50.0, ttft_s=1.0)
    assert b._estimate_ready
    assert b._decode_tps_ewma is not None
    assert b._ttft_ewma_s is not None
    derived = b._resolve_max_tokens(None, 5.0, 40)
    # (5.0 - ~1.0) * ~50 = ~200, floored by min_tokens=40, clamped by hard cap 1024.
    assert 150 <= derived <= 250


def test_explicit_max_tokens_overrides_measurement() -> None:
    b = _backend(max_tokens=60)
    _seed_estimator(b)
    assert b._resolve_max_tokens(300, 5.0, 40) == 300


def test_user_pin_beats_measurement() -> None:
    b = _backend(
        max_tokens=60,
        per_server_max_tokens={"http://localhost:11434/v1": 99},
    )
    _seed_estimator(b, decode_tps=100.0, ttft_s=0.5)
    assert b._resolve_max_tokens(None, 5.0, 40) == 99


def test_measurement_floored_to_min_tokens() -> None:
    b = _backend(max_tokens=60)
    # Very slow GPU → derived would be tiny without the floor.
    _seed_estimator(b, decode_tps=5.0, ttft_s=0.2)
    # (5.0 - 0.2) * 5 = 24, floored to min_tokens=40.
    derived = b._resolve_max_tokens(None, 5.0, 40)
    assert derived == 40


def test_target_below_ttft_clamps_to_min() -> None:
    b = _backend(max_tokens=60)
    _seed_estimator(b, decode_tps=50.0, ttft_s=4.0)
    # target=2s, ttft=4s → negative usable → floor at min_tokens.
    derived = b._resolve_max_tokens(None, 2.0, 40)
    assert derived == 40


def test_measurement_clamped_to_hard_cap() -> None:
    b = _backend(max_tokens=60)
    # Fast GPU, long budget → would exceed 1024.
    _seed_estimator(b, decode_tps=200.0, ttft_s=0.1)
    derived = b._resolve_max_tokens(None, 20.0, 40)
    assert derived == b._MAX_TOKENS_HARD_CAP


def test_measurement_clamped_to_context_quarter() -> None:
    b = _backend(max_tokens=60)
    b._context_length = 1024  # quarter = 256
    _seed_estimator(b, decode_tps=200.0, ttft_s=0.1)
    derived = b._resolve_max_tokens(None, 20.0, 40)
    assert derived == 256


def test_flag_off_ignores_target_latency() -> None:
    b = _backend(target_latency_scaling=False, max_tokens=60)
    _seed_estimator(b)
    assert b._resolve_max_tokens(None, 5.0, 40) == 60


def test_model_swap_clears_estimators() -> None:
    b = _backend(max_tokens=60)
    _seed_estimator(b)
    assert b._estimate_ready
    b.set_model("other-model")
    assert not b._estimate_ready
    assert b._decode_tps_ewma is None
    assert b._ttft_ewma_s is None


def test_set_api_url_clears_estimators() -> None:
    b = _backend(max_tokens=60)
    _seed_estimator(b)
    b.set_api_url("http://other:8585/v1")
    assert not b._estimate_ready


def test_ewma_tracks_thermal_drift() -> None:
    """Sustained slower samples should pull decode_tps down, not overwrite."""
    b = _backend(max_tokens=60)
    _seed_estimator(b, decode_tps=80.0, ttft_s=1.0)
    fast = b._decode_tps_ewma
    assert fast is not None
    # Feed many slower samples — thermal throttling scenario.
    for _ in range(20):
        b._record_sample(completion_tokens=40, total_elapsed_s=2.5)  # ~27 t/s after ttft
    slowed = b._decode_tps_ewma
    assert slowed is not None
    assert slowed < fast * 0.7


def test_ewma_absorbs_only_alpha_of_one_sample() -> None:
    """α=0.2: one 2x-faster sample moves EWMA by ~20%, not 100%."""
    b = _backend(max_tokens=60)
    _seed_estimator(b, decode_tps=50.0, ttft_s=1.0)
    # Sample decodes at 100 t/s (200 tokens in 2s after 1s ttft) — 2x steady.
    b._record_sample(completion_tokens=200, total_elapsed_s=3.0)
    # 0.8*50 + 0.2*100 = 60. One sample lifts EWMA ~20%, not all the way.
    assert b._decode_tps_ewma == pytest.approx(60.0, abs=0.5)


def test_cache_hit_sample_skipped_when_elapsed_below_ttft() -> None:
    """Cache-hit calls that finish faster than prior TTFT are dropped.

    Otherwise they poison the decode EWMA with inflated values.
    """
    b = _backend(max_tokens=60)
    _seed_estimator(b, decode_tps=50.0, ttft_s=2.0)
    before = b._decode_tps_ewma
    b._record_sample(completion_tokens=100, total_elapsed_s=1.0)  # < ttft
    assert b._decode_tps_ewma == before


def test_zero_completion_tokens_skipped() -> None:
    b = _backend(max_tokens=60)
    b._record_sample(completion_tokens=0, total_elapsed_s=2.0)
    assert b._sample_count == 0
    assert b._decode_tps_ewma is None


def test_pin_underuse_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    b = _backend(
        max_tokens=60,
        per_server_max_tokens={"http://localhost:11434/v1": 30},
    )
    _seed_estimator(b, decode_tps=100.0, ttft_s=0.5)
    import logging
    with caplog.at_level(logging.INFO, logger="tokenpal.llm.http_backend"):
        b._resolve_max_tokens(None, 5.0, 40)
        b._resolve_max_tokens(None, 5.0, 40)
    matches = [r for r in caplog.records if "leaves" in r.getMessage()]
    assert len(matches) == 1
