"""Tests for list_processes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tokenpal.actions.list_processes import _TOP_N_CAP, ListProcessesAction


class _FakeProc:
    def __init__(self, info: dict[str, Any]) -> None:
        self.info = info


def _mk(name: str, pid: int, cpu: float, rss_mb: float) -> _FakeProc:
    return _FakeProc({
        "name": name,
        "pid": pid,
        "cpu_percent": cpu,
        "memory_info": SimpleNamespace(rss=int(rss_mb * 1024 * 1024)),
    })


async def test_list_processes_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = [
        _mk("python", 1, 50.0, 200.0),
        _mk("firefox", 2, 20.0, 500.0),
        _mk("idle", 3, 0.1, 10.0),
    ]
    monkeypatch.setattr(
        "tokenpal.actions.list_processes.psutil.process_iter",
        lambda _attrs: iter(procs),
    )

    result = await ListProcessesAction({}).execute(top_n=2)
    assert result.success is True
    lines = result.output.splitlines()
    assert len(lines) == 2
    assert "python" in lines[0]
    assert "firefox" in lines[1]


async def test_list_processes_cap_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = [_mk(f"p{i}", i, float(i), float(i)) for i in range(100)]
    monkeypatch.setattr(
        "tokenpal.actions.list_processes.psutil.process_iter",
        lambda _attrs: iter(procs),
    )

    result = await ListProcessesAction({}).execute(top_n=9999)
    assert result.success is True
    assert len(result.output.splitlines()) == _TOP_N_CAP


async def test_list_processes_redacts_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = [_mk("1password-cli", 1, 99.0, 50.0)]
    monkeypatch.setattr(
        "tokenpal.actions.list_processes.psutil.process_iter",
        lambda _attrs: iter(procs),
    )

    result = await ListProcessesAction({}).execute(top_n=5)
    assert result.success is True
    assert "1password" not in result.output.lower()
    assert "something" in result.output


async def test_list_processes_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.list_processes.psutil.process_iter",
        lambda _attrs: iter([]),
    )
    result = await ListProcessesAction({}).execute()
    assert result.success is True
    assert "No processes" in result.output
