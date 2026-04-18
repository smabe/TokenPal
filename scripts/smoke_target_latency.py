"""Smoke-test target_latency_scaling against a live llama-server.

Runs 5 real generate calls, prints EWMA state + resolved max_tokens after
each. Use after flipping the flag in config.toml to verify the estimator
converges on real hardware.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from tokenpal.brain.memory import MemoryStore
from tokenpal.config.loader import load_config
from tokenpal.llm.http_backend import HttpBackend


PROMPTS = [
    "In one sentence, describe a rainy afternoon.",
    "Give a single-line weather forecast for Tokyo.",
    "Write one pithy observation about modern software.",
    "Name three fictional planets in one sentence.",
    "Share a one-liner about debugging.",
]


def _make_backend(llm: object, store: MemoryStore) -> HttpBackend:
    return HttpBackend({
        "api_url": llm.api_url,                          # type: ignore[attr-defined]
        "model_name": llm.model_name,                    # type: ignore[attr-defined]
        "max_tokens": llm.max_tokens,                    # type: ignore[attr-defined]
        "temperature": llm.temperature,                  # type: ignore[attr-defined]
        "disable_reasoning": llm.disable_reasoning,      # type: ignore[attr-defined]
        "inference_engine": llm.inference_engine,        # type: ignore[attr-defined]
        "target_latency_scaling": llm.target_latency_scaling,  # type: ignore[attr-defined]
        "per_server_models": llm.per_server_models,      # type: ignore[attr-defined]
        "per_server_max_tokens": llm.per_server_max_tokens,  # type: ignore[attr-defined]
        "memory_store": store,
    })


async def main() -> int:
    cfg = load_config()
    llm = cfg.llm

    tmp = Path(tempfile.mkdtemp()) / "smoke.db"
    store = MemoryStore(tmp)
    store.setup()

    backend = _make_backend(llm, store)

    await backend.setup()
    if not backend.is_reachable:
        print(f"ERROR: backend not reachable at {backend.api_url}", file=sys.stderr)
        return 1

    target_s = llm.target_latency_s.observation
    min_t = llm.min_tokens_per_path.observation
    print(f"server: {backend.api_url}")
    print(f"model: {backend.model_name}")
    print(f"context_length: {backend.context_length}")
    print(f"initial max_tokens: {backend.max_tokens}")
    print(f"target_latency_scaling: {llm.target_latency_scaling}")
    print(f"observation target: {target_s}s, min_tokens: {min_t}")
    print()

    for i, prompt in enumerate(PROMPTS, start=1):
        resp = await backend.generate(
            prompt, target_latency_s=target_s, min_tokens=min_t,
        )
        decode = backend._decode_tps_ewma
        ttft = backend._ttft_ewma_s
        ready = backend._estimate_ready
        resolved = backend._resolve_max_tokens(None, target_s, min_t)
        decode_str = f"{decode:.1f}" if decode is not None else "—"
        ttft_str = f"{ttft:.2f}" if ttft is not None else "—"
        print(
            f"call {i}: "
            f"elapsed={resp.latency_ms / 1000:.2f}s "
            f"completion≈{resp.tokens_used} tokens "
            f"decode_tps={decode_str} "
            f"ttft_s={ttft_str} "
            f"n={backend._sample_count} "
            f"ready={ready} "
            f"resolved_max={resolved}"
        )
        print(f"  reply: {resp.text[:80]}")

    await backend.teardown()

    # Force a final writeback (throttle bypass) so the persistence path runs
    # even on a fast smoke run.
    backend._last_writeback_s = 0.0
    backend._maybe_persist_estimator()

    print()
    print("--- second backend, same store, should seed immediately ---")
    b2 = _make_backend(llm, store)
    await b2.setup()
    decode = b2._decode_tps_ewma
    ttft = b2._ttft_ewma_s
    print(
        f"after setup: ready={b2._estimate_ready} "
        f"decode_tps={decode and f'{decode:.1f}'} "
        f"ttft_s={ttft and f'{ttft:.2f}'} "
        f"n={b2._sample_count}"
    )
    target_s = llm.target_latency_s.observation
    min_t = llm.min_tokens_per_path.observation
    resp = await b2.generate(
        PROMPTS[0], target_latency_s=target_s, min_tokens=min_t,
    )
    resolved = b2._resolve_max_tokens(None, target_s, min_t)
    print(
        f"call 1: elapsed={resp.latency_ms / 1000:.2f}s "
        f"resolved_max={resolved} (no bootstrap burn)"
    )
    await b2.teardown()
    store.teardown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
