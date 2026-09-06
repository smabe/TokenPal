"""ToolInvoker: rate limits, usage recording."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from tokenpal.actions.base import AbstractAction, ActionResult, RateLimit
from tokenpal.actions.invoker import ToolInvoker


class _Counter(AbstractAction):
    action_name: ClassVar[str] = "counter"
    description: ClassVar[str] = "test"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}
    safe: ClassVar[bool] = True
    requires_confirm: ClassVar[bool] = False
    rate_limit: ClassVar[RateLimit | None] = RateLimit(max_calls=2, window_s=10.0)

    calls = 0

    async def execute(self, **_: object) -> ActionResult:
        _Counter.calls += 1
        return ActionResult(output=str(_Counter.calls))


@pytest.mark.asyncio
async def test_rate_limit_fails_third_call_within_window() -> None:
    _Counter.calls = 0
    invoker = ToolInvoker()
    action = _Counter({})

    r1 = await invoker.invoke(action, {})
    r2 = await invoker.invoke(action, {})
    r3 = await invoker.invoke(action, {})

    assert r1.success and r1.output == "1"
    assert r2.success and r2.output == "2"
    assert not r3.success
    assert "rate limit" in r3.output
    # Third call never reached the action body.
    assert _Counter.calls == 2


@pytest.mark.asyncio
async def test_on_call_records_usage() -> None:
    _Counter.calls = 0
    records: list[tuple[str, float, bool]] = []
    invoker = ToolInvoker(on_call=lambda name, dur, ok: records.append((name, dur, ok)))
    action = _Counter({})
    await invoker.invoke(action, {})
    assert len(records) == 1
    name, duration, ok = records[0]
    assert name == "counter"
    assert ok is True
    assert duration >= 0.0


@pytest.mark.asyncio
async def test_on_call_exception_does_not_break_invoke() -> None:
    _Counter.calls = 0

    def _boom(*_args: object) -> None:
        raise RuntimeError("oh no")

    invoker = ToolInvoker(on_call=_boom)
    action = _Counter({})
    result = await invoker.invoke(action, {})
    assert result.success


@pytest.mark.asyncio
async def test_window_release_allows_retry() -> None:
    _Counter.calls = 0

    class _Tiny(_Counter):
        action_name: ClassVar[str] = "tiny"
        rate_limit: ClassVar[RateLimit | None] = RateLimit(max_calls=1, window_s=0.05)

    invoker = ToolInvoker()
    action = _Tiny({})
    r1 = await invoker.invoke(action, {})
    r2 = await invoker.invoke(action, {})
    assert r1.success
    assert not r2.success
    await asyncio.sleep(0.06)
    r3 = await invoker.invoke(action, {})
    assert r3.success


class _Limited(_Counter):
    """Two calls per two minutes — the shard's observable stub."""

    action_name: ClassVar[str] = "limited"
    rate_limit: ClassVar[RateLimit | None] = RateLimit(max_calls=2, window_s=120.0)


@pytest.mark.asyncio
async def test_enforce_rate_limit_false_skips_the_limit() -> None:
    """Chat and the ambient tick construct the invoker this way: the user is
    present and asked, so a declared limit is not enforced there."""
    _Counter.calls = 0
    invoker = ToolInvoker(enforce_rate_limit=False)
    action = _Limited({})

    results = [await invoker.invoke(action, {}) for _ in range(3)]

    assert [r.success for r in results] == [True, True, True]
    assert [r.output for r in results] == ["1", "2", "3"]
    assert _Counter.calls == 3


@pytest.mark.asyncio
async def test_default_still_enforces_the_limit() -> None:
    _Counter.calls = 0
    invoker = ToolInvoker()
    action = _Limited({})

    results = [await invoker.invoke(action, {}) for _ in range(3)]

    assert [r.success for r in results] == [True, True, False]
    assert results[2].output == "rate limit: 2 calls per 120s exceeded"
    assert _Counter.calls == 2
