"""Tests for the proactive git WIP nudge."""

from __future__ import annotations

import time

import pytest

from tokenpal.brain.git_nudge import GitNudgeDetector, GitNudgeSignal
from tokenpal.config.schema import GitNudgeConfig
from tokenpal.senses.base import SenseReading


def _git_reading(
    branch: str = "main",
    dirty: bool = True,
    last_commit_ts: float | None = None,
    last_commit_msg: str = "WIP: stub",
) -> SenseReading:
    return SenseReading(
        sense_name="git",
        timestamp=time.monotonic(),
        data={
            "branch": branch,
            "dirty": dirty,
            "last_commit_ts": last_commit_ts,
            "last_commit_msg": last_commit_msg,
        },
        summary=f"On branch {branch}",
        changed_from="",
    )


@pytest.fixture()
def config() -> GitNudgeConfig:
    return GitNudgeConfig(
        enabled=True,
        wip_stale_hours=1.0 / 3600,  # 1 second for fast tests
        cooldown_s=60.0,
        wip_markers=["wip", "tmp", "todo"],
    )


# ---------------------------------------------------------------------


def test_disabled_never_fires(config: GitNudgeConfig) -> None:
    config.enabled = False
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(last_commit_ts=time.time() - 10_000)])
    assert d.check(user_present=True) is None


def test_user_absent_never_fires(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(last_commit_ts=time.time() - 10_000)])
    assert d.check(user_present=False) is None


def test_non_wip_message_never_fires(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    d.ingest(
        [
            _git_reading(
                last_commit_ts=time.time() - 10_000,
                last_commit_msg="Fix the auth race condition",
            )
        ]
    )
    assert d.check(user_present=True) is None


def test_clean_tree_never_fires(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(dirty=False, last_commit_ts=time.time() - 10_000)])
    assert d.check(user_present=True) is None


def test_fresh_wip_never_fires(config: GitNudgeConfig) -> None:
    """Commit just landed; stale window not yet reached."""
    config.wip_stale_hours = 10.0
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(last_commit_ts=time.time())])
    assert d.check(user_present=True) is None


def test_stale_wip_fires(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    # wip_stale_hours = 1s in the fixture; offset by 10s
    d.ingest(
        [
            _git_reading(
                last_commit_ts=time.time() - 10,
                last_commit_msg="WIP: sketch the detector",
            )
        ]
    )
    signal = d.check(user_present=True)
    assert isinstance(signal, GitNudgeSignal)
    assert signal.branch == "main"
    assert "WIP" in signal.last_commit_msg
    assert signal.stale_hours > 0


def test_cooldown_enforced(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(last_commit_ts=time.time() - 10)])
    first = d.check(user_present=True)
    assert first is not None
    d.mark_emitted()
    # Still stale, still dirty — but cooldown blocks the second fire
    second = d.check(user_present=True)
    assert second is None


def test_wip_marker_match_is_case_insensitive(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    d.ingest(
        [_git_reading(last_commit_ts=time.time() - 10, last_commit_msg="wip: draft")]
    )
    assert d.check(user_present=True) is not None


def test_ingest_updates_cache(config: GitNudgeConfig) -> None:
    """Sequential readings update state incrementally."""
    d = GitNudgeDetector(config=config)
    d.ingest([_git_reading(dirty=False, last_commit_ts=time.time() - 10)])
    assert d.check(user_present=True) is None  # not dirty
    d.ingest([_git_reading(dirty=True, last_commit_ts=time.time() - 10)])
    assert d.check(user_present=True) is not None  # now dirty


def test_non_git_readings_ignored(config: GitNudgeConfig) -> None:
    d = GitNudgeDetector(config=config)
    other = SenseReading(
        sense_name="app_awareness",
        timestamp=time.monotonic(),
        data={"app_name": "VS Code"},
        summary="VS Code",
    )
    d.ingest([other])
    assert d.check(user_present=True) is None
