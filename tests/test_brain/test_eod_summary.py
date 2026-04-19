"""Tests for the end-of-day summary generator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tokenpal.brain.eod_summary import EODSummary, today_str, yesterday_str
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import LLMResponse


@dataclass
class FakeLLM:
    reply: str = "Solid day of pushing pixels."

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    async def generate(
        self,
        prompt: str,
        max_tokens: int | None = None,
        *,
        enable_thinking: bool | None = None,
        response_format: dict[str, Any] | None = None,
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(prompt)
        return LLMResponse(
            text=self.reply,
            tokens_used=10,
            model_name="fake",
            latency_ms=10.0,
        )


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "m.db")
    s.setup()
    return s


@pytest.fixture()
def personality() -> PersonalityEngine:
    return PersonalityEngine("You are a test buddy.")


def _insert_app_switch(
    memory: MemoryStore, app: str, ts: float, session_id: str
) -> None:
    assert memory._conn is not None
    memory._conn.execute(
        "INSERT INTO observations "
        "(timestamp, sense_name, event_type, summary, data_json, session_id) "
        "VALUES (?, 'app_awareness', 'app_switch', ?, NULL, ?)",
        (ts, app, session_id),
    )
    memory._conn.commit()


def _ts_for(date_str: str, hour: int = 12) -> float:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour).timestamp()


# -----------------------------------------------------------------------


def test_empty_day_is_silent(
    memory: MemoryStore, personality: PersonalityEngine
) -> None:
    """A date with no observations produces no bubble and no LLM call."""
    llm = FakeLLM()
    eod = EODSummary(memory=memory, llm=llm, personality=personality)
    async def run() -> str | None:
        return await eod.generate(yesterday_str())
    import asyncio
    result = asyncio.run(run())
    assert result is None
    assert llm.calls == []


def test_populated_day_produces_bubble(
    memory: MemoryStore, personality: PersonalityEngine
) -> None:
    llm = FakeLLM(reply="Code ran, you didn't — usual ratio.")
    eod = EODSummary(memory=memory, llm=llm, personality=personality)
    yesterday = yesterday_str()
    _insert_app_switch(memory, "VS Code", _ts_for(yesterday, 10), "y1")
    _insert_app_switch(memory, "Chrome", _ts_for(yesterday, 11), "y1")
    import asyncio
    result = asyncio.run(eod.generate(yesterday))
    assert result is not None
    assert "ratio" in result
    assert len(llm.calls) == 1
    # The prompt should include the top apps as factual context
    assert "VS Code" in llm.calls[0]


def test_sensitive_term_drops_bubble(
    memory: MemoryStore, personality: PersonalityEngine
) -> None:
    llm = FakeLLM(reply="You spent 20 minutes in 1Password rotating keys.")
    eod = EODSummary(memory=memory, llm=llm, personality=personality)
    yesterday = yesterday_str()
    _insert_app_switch(memory, "VS Code", _ts_for(yesterday, 10), "y1")
    import asyncio
    result = asyncio.run(eod.generate(yesterday))
    assert result is None


def test_digest_counts_sessions_and_idle(memory: MemoryStore) -> None:
    yesterday = yesterday_str()
    _insert_app_switch(memory, "VS Code", _ts_for(yesterday, 9), "y1")
    _insert_app_switch(memory, "VS Code", _ts_for(yesterday, 11), "y1")
    _insert_app_switch(memory, "Chrome", _ts_for(yesterday, 14), "y2")
    assert memory._conn is not None
    memory._conn.execute(
        "INSERT INTO observations "
        "(timestamp, sense_name, event_type, summary, data_json, session_id) "
        "VALUES (?, 'idle', 'idle_return', 'back', NULL, 'y1')",
        (_ts_for(yesterday, 12),),
    )
    memory._conn.commit()

    digest = memory.get_day_digest(yesterday)
    assert digest["session_count"] == 2
    assert digest["idle_returns"] == 1
    top = dict(digest["apps"])
    assert top["VS Code"] == 2
    assert top["Chrome"] == 1


def test_has_shown_eod_round_trip(memory: MemoryStore) -> None:
    assert memory.has_shown_eod("2026-01-01") is False
    memory.mark_eod_shown("2026-01-01")
    assert memory.has_shown_eod("2026-01-01") is True
    # Different dates don't collide
    assert memory.has_shown_eod("2026-01-02") is False


def test_today_and_yesterday_helpers() -> None:
    today = today_str()
    yesterday = yesterday_str()
    assert today != yesterday
    parsed_today = datetime.strptime(today, "%Y-%m-%d")
    parsed_yest = datetime.strptime(yesterday, "%Y-%m-%d")
    assert (parsed_today - parsed_yest) == timedelta(days=1)


def test_llm_failure_returns_none(
    memory: MemoryStore, personality: PersonalityEngine
) -> None:
    class FailingLLM(FakeLLM):
        async def generate(self, prompt: str, *args: Any, **kwargs: Any) -> LLMResponse:
            raise RuntimeError("boom")
    llm = FailingLLM()
    eod = EODSummary(memory=memory, llm=llm, personality=personality)
    yesterday = yesterday_str()
    _insert_app_switch(memory, "VS Code", _ts_for(yesterday, 10), "y1")
    import asyncio
    result = asyncio.run(eod.generate(yesterday))
    assert result is None
