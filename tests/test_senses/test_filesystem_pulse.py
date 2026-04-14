"""Tests for the filesystem_pulse sense."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenpal.senses.filesystem_pulse.sense import (
    _BURST_THRESHOLD,
    FilesystemPulse,
    _is_excluded_path,
)


def _make(roots: list[str] | None = None) -> FilesystemPulse:
    sense = FilesystemPulse({"roots": roots or []})
    return sense


def _seed_root(sense: FilesystemPulse, root: str, leaf: str = "target") -> None:
    """Register a root with the sense (skips watchdog wiring)."""
    sense._root_leaf[root] = leaf


def test_is_excluded_path() -> None:
    assert _is_excluded_path("/home/user/project/node_modules/foo.js")
    assert _is_excluded_path("/home/user/project/.git/HEAD")
    assert _is_excluded_path("/tmp/build/out.bin")
    assert not _is_excluded_path("/home/user/project/src/main.py")
    assert not _is_excluded_path("/home/user/Downloads/report.pdf")


def test_record_event_skips_excluded() -> None:
    sense = _make()
    _seed_root(sense, "/watched")
    for _ in range(_BURST_THRESHOLD + 2):
        sense._record_event("/watched/node_modules/x.js")
    assert sense._pending_bursts == []


def test_record_event_skips_unwatched_path() -> None:
    sense = _make()
    _seed_root(sense, "/watched")
    for _ in range(_BURST_THRESHOLD + 2):
        sense._record_event("/somewhere/else/a.txt")
    assert sense._pending_bursts == []


async def test_burst_triggers_reading() -> None:
    sense = _make()
    _seed_root(sense, "/watched", leaf="Downloads")
    for i in range(_BURST_THRESHOLD):
        sense._record_event(f"/watched/file{i}.txt")

    reading = await sense.poll()
    assert reading is not None
    assert reading.data["event"] == "activity_burst"
    assert reading.data["root"] == "Downloads"
    assert reading.data["count"] >= _BURST_THRESHOLD


async def test_burst_cooldown_prevents_reemission() -> None:
    sense = _make()
    _seed_root(sense, "/watched")
    for i in range(_BURST_THRESHOLD):
        sense._record_event(f"/watched/f{i}.txt")
    first = await sense.poll()
    assert first is not None

    # Immediate follow-up burst should NOT generate a second pending reading.
    for i in range(_BURST_THRESHOLD):
        sense._record_event(f"/watched/g{i}.txt")
    second = await sense.poll()
    assert second is None


async def test_no_pending_returns_none() -> None:
    sense = _make()
    _seed_root(sense, "/watched")
    assert await sense.poll() is None


async def test_below_threshold_does_not_emit() -> None:
    sense = _make()
    _seed_root(sense, "/watched")
    for i in range(_BURST_THRESHOLD - 1):
        sense._record_event(f"/watched/f{i}.txt")
    assert await sense.poll() is None


async def test_setup_disables_when_no_roots(tmp_path, monkeypatch) -> None:
    sense = _make(roots=[])
    monkeypatch.setattr(
        "tokenpal.config.paths.default_watch_roots",
        lambda: [],
    )
    await sense.setup()
    assert sense.enabled is False


def test_privacy_no_full_path_in_summary() -> None:
    """Summaries must not leak paths — only leaf dir name."""
    sense = _make()
    _seed_root(sense, "/Users/alice/Secret/Projects/internal", leaf="internal")
    for i in range(_BURST_THRESHOLD):
        sense._record_event(f"/Users/alice/Secret/Projects/internal/file{i}.py")
    import asyncio
    reading = asyncio.run(sense.poll())
    assert reading is not None
    assert "/Users/alice" not in reading.summary
    assert "Secret" not in reading.summary
    assert "internal" in reading.summary
