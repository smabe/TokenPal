"""Tests for the periodic session summarizer."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.session_summarizer import SessionSummarizer
from tokenpal.llm.base import LLMResponse


@dataclass
class FakeLLMResponse:
    text: str


class FakeLLM:
    """Minimal AbstractLLMBackend stand-in for the summarizer."""

    def __init__(self, reply: str = "User worked on TokenPal in VS Code.") -> None:
        self.reply = reply
        self.calls: list[str] = []
        self.raise_next = False

    @property
    def model_name(self) -> str:
        return "fake"

    async def setup(self) -> None:
        return None

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
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")
        return LLMResponse(
            text=self.reply,
            tokens_used=10,
            model_name="fake",
            latency_ms=10.0,
        )

    async def teardown(self) -> None:
        return None


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "m.db")
    s.setup()
    return s


def _insert_obs(memory: MemoryStore, ts: float, summary: str = "VS Code") -> None:
    assert memory._conn is not None
    memory._conn.execute(
        "INSERT INTO observations "
        "(timestamp, sense_name, event_type, summary, data_json, session_id) "
        "VALUES (?, 'app_awareness', 'app_switch', ?, NULL, ?)",
        (ts, summary, memory.session_id),
    )
    memory._conn.commit()


@pytest.mark.asyncio
async def test_skip_if_idle_writes_nothing(memory: MemoryStore) -> None:
    llm = FakeLLM()
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    # Force a non-trivial elapsed window
    summarizer._window_start = time.time() - 120
    await summarizer._tick()
    assert llm.calls == [], "no LLM call should be made on an idle window"
    assert memory.get_recent_summaries(since_ts=0.0) == []


@pytest.mark.asyncio
async def test_writes_summary_on_activity(memory: MemoryStore) -> None:
    llm = FakeLLM(reply="You wrestled with the auth PR in VS Code.")
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    window_start = time.time() - 120
    summarizer._window_start = window_start
    _insert_obs(memory, window_start + 10, "VS Code")
    _insert_obs(memory, window_start + 60, "Chrome")

    await summarizer._tick()

    assert len(llm.calls) == 1
    rows = memory.get_recent_summaries(since_ts=0.0)
    assert len(rows) == 1
    assert "auth PR" in rows[0][1]


@pytest.mark.asyncio
async def test_sensitive_term_drop(memory: MemoryStore) -> None:
    # "1password" is in SENSITIVE_APPS
    llm = FakeLLM(reply="User spent 5 minutes in 1Password rotating keys.")
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    window_start = time.time() - 120
    summarizer._window_start = window_start
    _insert_obs(memory, window_start + 10, "VS Code")

    await summarizer._tick()

    assert len(llm.calls) == 1
    assert memory.get_recent_summaries(since_ts=0.0) == []


@pytest.mark.asyncio
async def test_none_reply_skipped(memory: MemoryStore) -> None:
    llm = FakeLLM(reply="NONE")
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    window_start = time.time() - 120
    summarizer._window_start = window_start
    _insert_obs(memory, window_start + 10, "VS Code")

    await summarizer._tick()

    assert len(llm.calls) == 1
    assert memory.get_recent_summaries(since_ts=0.0) == []


@pytest.mark.asyncio
async def test_llm_error_keeps_window_open(memory: MemoryStore) -> None:
    llm = FakeLLM()
    llm.raise_next = True
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    original_start = time.time() - 120
    summarizer._window_start = original_start
    _insert_obs(memory, original_start + 10, "VS Code")

    await summarizer._tick()

    # Window should NOT have advanced — retry with same range next tick.
    assert summarizer._window_start == original_start
    assert memory.get_recent_summaries(since_ts=0.0) == []


@pytest.mark.asyncio
async def test_advances_window_on_success(memory: MemoryStore) -> None:
    llm = FakeLLM()
    summarizer = SessionSummarizer(memory=memory, llm=llm, interval_s=60)
    window_start = time.time() - 120
    summarizer._window_start = window_start
    _insert_obs(memory, window_start + 10, "VS Code")

    await summarizer._tick()

    assert summarizer._window_start > window_start
