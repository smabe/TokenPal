"""ToolInvoker: rate limits, usage recording."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

from tokenpal.actions.base import AbstractAction, ActionResult, RateLimit
from tokenpal.actions.invoker import ToolInvoker
from tokenpal.util.paths import ResolvedPath


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


# --- declared path policy ---


class _PathTool(AbstractAction):
    """Declares one required path; records what ``execute`` was handed."""

    action_name: ClassVar[str] = "path_tool"
    description: ClassVar[str] = "test"
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    safe: ClassVar[bool] = True
    requires_confirm: ClassVar[bool] = False
    path_params: ClassVar[tuple[str, ...]] = ("path",)
    path_roots: ClassVar[str] = "git_root"
    path_screen: ClassVar[str] = "broad"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.seen: list[object] = []

    async def execute(self, **kwargs: object) -> ActionResult:
        self.seen.append(kwargs.get("path"))
        return ActionResult(output="ran")


class _NoPathTool(_PathTool):
    action_name: ClassVar[str] = "no_path_tool"
    path_params: ClassVar[tuple[str, ...]] = ()


def _stub_repo(monkeypatch: pytest.MonkeyPatch, repo: Path | None) -> list[int]:
    calls: list[int] = []

    async def fake(_start: Path) -> Path | None:
        calls.append(1)
        return repo

    monkeypatch.setattr("tokenpal.util.paths.git_root", fake)
    return calls


async def test_a_declared_path_arrives_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_text("x")
    _stub_repo(monkeypatch, tmp_path)
    action = _PathTool({})

    result = await ToolInvoker().invoke(action, {"path": "a.txt"})

    assert result.success is True
    handed = action.seen[0]
    assert isinstance(handed, ResolvedPath)
    assert (handed.raw, handed.rel) == ("a.txt", "a.txt")
    assert handed.resolved == (tmp_path / "a.txt").resolve()


async def test_substitution_does_not_mutate_the_caller_s_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chat dispatcher logs ``tc.arguments`` after the call; a resolved
    absolute path must not end up in that line."""
    _stub_repo(monkeypatch, tmp_path)
    arguments = {"path": "a.txt"}

    await ToolInvoker().invoke(_PathTool({}), arguments)

    assert arguments == {"path": "a.txt"}


async def test_a_path_outside_the_roots_never_reaches_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _stub_repo(monkeypatch, repo)
    action = _PathTool({})

    result = await ToolInvoker().invoke(action, {"path": str(outside / "a.txt")})

    assert result.success is False
    assert action.seen == []


async def test_an_undeclared_tool_does_no_path_work(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """``path_params`` empty is the gate that keeps a git subprocess off the
    tools that declare no path, including every idle roll."""
    calls = _stub_repo(monkeypatch, None)
    action = _NoPathTool({})

    result = await ToolInvoker().invoke(action, {"path": "../../etc/passwd"})

    assert result.success is True
    assert action.seen == ["../../etc/passwd"]
    assert calls == []


async def test_a_blank_path_is_left_to_the_tool(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """An optional or missing path is the tool's refusal to word, and
    grep_codebase's ``path`` is genuinely optional."""
    calls = _stub_repo(monkeypatch, None)
    action = _PathTool({})

    assert (await ToolInvoker().invoke(action, {"path": "  "})).success is True
    assert (await ToolInvoker().invoke(action, {})).success is True
    assert action.seen == ["  ", None]
    assert calls == []


async def test_containment_runs_before_the_rate_limit_slot_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rate-limit block must stay await-free, so containment goes strictly
    before it — and a refused path therefore burns no slot."""

    class _LimitedPath(_PathTool):
        action_name: ClassVar[str] = "limited_path"
        rate_limit: ClassVar[RateLimit | None] = RateLimit(max_calls=1, window_s=120.0)

    _stub_repo(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("x")
    invoker = ToolInvoker()
    action = _LimitedPath({})

    refused = await invoker.invoke(action, {"path": ".env"})
    allowed = await invoker.invoke(action, {"path": "a.txt"})

    assert refused.success is False
    assert allowed.success is True
