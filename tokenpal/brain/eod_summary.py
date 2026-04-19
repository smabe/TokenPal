"""End-of-day summary — a one-bubble-per-day reflection in the buddy's voice.

Fires once per local date, either:
 * Automatically on the first startup of a new local day when the prior day
   has any activity to summarize (tracked via MemoryStore.has_shown_eod).
 * On demand via the ``/summary [today|yesterday]`` slash command.

Design notes (see plans/buddy-utility-wedges.md):
 * Outside the normal pacing gate. Once-a-day is self-limiting.
 * Silent no-op when the target date has no activity — no empty-day bubble.
 * All activity stats come from MemoryStore.get_day_digest; no direct SQL.
 * Privacy: sensitive-app names never enter the digest because the existing
   filter already drops those observations upstream. We still run the final
   text through ``contains_sensitive_term`` defensively before emitting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import PersonalityEngine, contains_sensitive_term
from tokenpal.llm.base import AbstractLLMBackend

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DayDigest:
    """Facts for the day the buddy can riff on."""

    date: str
    top_apps: list[tuple[str, int]]
    session_count: int
    active_minutes: int
    idle_returns: int
    last_summary: str | None


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _digest_from_memory(memory: MemoryStore, date_str: str) -> DayDigest:
    raw = memory.get_day_digest(date_str)
    return DayDigest(
        date=raw["date"],
        top_apps=raw["apps"],
        session_count=raw["session_count"],
        active_minutes=raw["active_minutes"],
        idle_returns=raw["idle_returns"],
        last_summary=raw["last_summary"],
    )


_EOD_TEMPLATE = """\
You are the buddy. Give the user a SHORT end-of-day reflection (1-2 sentences)
in your voice. Be observational, dry, or affectionate — whatever matches your
character. No lists, no stats, no greetings like "hey" or "so". Just a line.

Day recap for {date}:
- Top apps: {apps}
- Sessions: {session_count}, {active_minutes} minutes active
- Idle returns: {idle_returns}
{summary_line}

Your line:"""


def _format_prompt(digest: DayDigest) -> str:
    if digest.top_apps:
        apps = ", ".join(f"{name} ({count})" for name, count in digest.top_apps)
    else:
        apps = "nothing interesting"
    summary_line = (
        f"- Earlier handoff note: {digest.last_summary}"
        if digest.last_summary
        else ""
    )
    return _EOD_TEMPLATE.format(
        date=digest.date,
        apps=apps,
        session_count=digest.session_count,
        active_minutes=digest.active_minutes,
        idle_returns=digest.idle_returns,
        summary_line=summary_line,
    )


class EODSummary:
    """Generates end-of-day summary bubbles on demand."""

    def __init__(
        self,
        memory: MemoryStore,
        llm: AbstractLLMBackend,
        personality: PersonalityEngine,
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._personality = personality
        self._target_latency_s = target_latency_s
        self._min_tokens = min_tokens

    def digest_for(self, date_str: str) -> DayDigest:
        return _digest_from_memory(self._memory, date_str)

    async def generate(self, date_str: str) -> str | None:
        """Produce the EOD bubble for date_str, or None when the day had no
        activity (empty top apps AND no sessions = silent no-op).
        """
        digest = self.digest_for(date_str)
        if not digest.top_apps and digest.session_count == 0:
            log.debug("EOD skipped — no activity for %s", date_str)
            return None

        prompt = _format_prompt(digest)
        try:
            response = await self._llm.generate(
                prompt,
                target_latency_s=self._target_latency_s,
                min_tokens=self._min_tokens,
            )
        except Exception:
            log.exception("EOD LLM call failed for %s", date_str)
            return None

        raw = (response.text or "").strip()
        if not raw:
            return None
        filtered = self._personality.filter_response(raw)
        if not filtered:
            return None
        if contains_sensitive_term(filtered):
            log.info("EOD dropped — sensitive term in response")
            return None
        return filtered
