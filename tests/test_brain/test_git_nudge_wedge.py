from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock

import pytest

from tokenpal.brain.git_nudge import GitNudgeSignal
from tokenpal.brain.wedge import EmissionCandidate, PromptContext
from tokenpal.brain.wedges.git_nudge import GitNudgeWedge
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
        wip_markers=["wip"],
    )


def test_propose_returns_none_when_disabled(config: GitNudgeConfig) -> None:
    config.enabled = False
    w = GitNudgeWedge(config=config)
    w.ingest([_git_reading(last_commit_ts=time.time() - 10_000)])
    assert w.propose() is None


def test_propose_returns_none_without_user_present(
    config: GitNudgeConfig,
) -> None:
    w = GitNudgeWedge(config=config)
    # ingest with empty list — _user_present stays False, so propose skips
    w.ingest([])
    assert w.propose() is None


def test_propose_returns_candidate_when_wip_stale(
    config: GitNudgeConfig,
) -> None:
    w = GitNudgeWedge(config=config)
    w.ingest([_git_reading(last_commit_ts=time.time() - 10_000)])
    cand = w.propose()
    assert isinstance(cand, EmissionCandidate)
    assert cand.wedge_name == "git_nudge"
    assert isinstance(cand.payload, GitNudgeSignal)


def test_on_emitted_starts_cooldown(config: GitNudgeConfig) -> None:
    w = GitNudgeWedge(config=config)
    w.ingest([_git_reading(last_commit_ts=time.time() - 10_000)])
    cand = w.propose()
    assert cand is not None
    w.on_emitted(cand)
    # Cooldown should suppress the next propose even with the same conditions.
    assert w.propose() is None


def test_build_prompt_delegates_to_personality(config: GitNudgeConfig) -> None:
    w = GitNudgeWedge(config=config)
    sig = GitNudgeSignal(
        branch="feature/x", last_commit_msg="wip: tests", stale_hours=2.5,
    )
    cand = EmissionCandidate(wedge_name="git_nudge", payload=sig)
    personality = Mock()
    personality.build_git_nudge_prompt.return_value = "wrap it up"
    ctx = PromptContext(personality=personality, snapshot="")
    prompt = w.build_prompt(cand, ctx)
    assert prompt == "wrap it up"
    personality.build_git_nudge_prompt.assert_called_once_with(
        branch="feature/x", commit_msg="wip: tests", stale_hours=2.5,
    )


async def test_hydrate_delegates_to_detector(config: GitNudgeConfig) -> None:
    w = GitNudgeWedge(config=config)
    fake = Mock()
    fake.hydrate = AsyncMock()
    w._detector = fake  # type: ignore[assignment]
    await w.hydrate()
    fake.hydrate.assert_awaited_once()
