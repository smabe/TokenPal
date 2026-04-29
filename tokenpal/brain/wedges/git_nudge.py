"""GitNudgeWedge — wraps GitNudgeDetector behind the Wedge interface."""

from __future__ import annotations

from typing import cast

from tokenpal.brain.git_nudge import GitNudgeDetector, GitNudgeSignal
from tokenpal.brain.wedge import (
    EmissionCandidate,
    GatePolicy,
    PromptContext,
    Wedge,
)
from tokenpal.config.schema import GitNudgeConfig
from tokenpal.senses.base import SenseReading


class GitNudgeWedge(Wedge):
    name = "git_nudge"
    priority = 85
    gate = GatePolicy.BYPASS_CAP

    def __init__(self, config: GitNudgeConfig | None = None) -> None:
        self._detector = GitNudgeDetector(config or GitNudgeConfig())
        self._user_present = False

    @property
    def enabled(self) -> bool:
        return self._detector.enabled

    async def hydrate(self) -> None:
        await self._detector.hydrate()

    def ingest(self, readings: list[SenseReading]) -> None:
        self._detector.ingest(readings)
        if readings:
            self._user_present = True

    def propose(self) -> EmissionCandidate | None:
        if not self._detector.enabled:
            return None
        sig = self._detector.check(user_present=self._user_present)
        if sig is None:
            return None
        return EmissionCandidate(wedge_name=self.name, payload=sig)

    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str:
        sig = cast(GitNudgeSignal, candidate.payload)
        return ctx.personality.build_git_nudge_prompt(
            branch=sig.branch,
            commit_msg=sig.last_commit_msg,
            stale_hours=sig.stale_hours,
        )

    def on_emitted(self, candidate: EmissionCandidate) -> None:
        self._detector.mark_emitted()
