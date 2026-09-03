"""Shared test doubles.

Importable from any test:

    from tests._helpers import ScriptedLLM, ok_response

Resolves issue #37 - was previously redefined inline in five test
modules. Reply-continuation tests still hold their own variant because
its script shape (text + finish_reason tuples) is structurally distinct.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tokenpal.brain.memory import MemoryStore
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse
from tokenpal.senses.web_search.client import SearchResult


class ScriptedLLM(AbstractLLMBackend):
    """Returns pre-queued LLMResponse objects in order.

    Both `generate` and `generate_with_tools` consume from the same queue.
    Tracks `prompts`, `call_kwargs`, and `calls` so tests can assert on
    invocation history. Pass `forbid_tools=True` for paths that must
    never reach for `generate_with_tools` (research/planner).
    """

    backend_name = "scripted"
    platforms = ("darwin", "linux", "windows")

    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        forbid_tools: bool = False,
    ) -> None:
        super().__init__({})
        self._responses = list(responses)
        self._forbid_tools = forbid_tools
        self.prompts: list[str] = []
        self.call_kwargs: list[dict[str, Any]] = []
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    async def generate(  # type: ignore[override]
        self, prompt: str, max_tokens: int = 256, **kwargs: Any,
    ) -> LLMResponse:
        self.prompts.append(prompt)
        self.call_kwargs.append({"max_tokens": max_tokens, **kwargs})
        if not self._responses:
            return LLMResponse(text="", tokens_used=0, model_name="t", latency_ms=0)
        return self._responses.pop(0)

    async def generate_with_tools(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if self._forbid_tools:
            raise AssertionError(
                "this path must not use generate_with_tools",
            )
        self.calls.append((list(messages), list(tools)))
        self.call_kwargs.append({"max_tokens": max_tokens, **kwargs})
        if not self._responses:
            return LLMResponse(text="", tokens_used=0, model_name="t", latency_ms=0)
        return self._responses.pop(0)


def ok_response(text: str, tokens: int = 10) -> LLMResponse:
    """One-line LLMResponse builder, used by research test fixtures."""
    return LLMResponse(text=text, tokens_used=tokens, model_name="t", latency_ms=0)


def search_hit(
    url: str, title: str, text: str, backend: str = "duckduckgo",
) -> SearchResult:
    """Minimal SearchResult for test scaffolding."""
    return SearchResult(
        query="q",
        backend=backend,  # type: ignore[arg-type]
        title=title,
        text=text,
        source_url=url,
    )


async def noop_fetch(_url: str) -> str | None:
    """Fetcher that returns None - simulates "no body extracted"."""
    return None


def capture_logs() -> tuple[list[str], Any]:
    """Returns (buffer, log_callback) so tests can assert on log output."""
    buf: list[str] = []

    def _cb(
        msg: str,
        *,
        markup: bool = False,
        url: str | None = None,
        persist: bool = True,
    ) -> None:
        line = f"{msg} <{url}>" if url else msg
        buf.append(line if persist else f"{line} [unpersisted]")

    return buf, _cb


def assert_no_leak(
    fixture: str,
    *,
    lines: list[str],
    caplog_text: str,
    memory: MemoryStore | None = None,
) -> None:
    """Assert *fixture* reached no sink: not a persisted trace line, not the
    captured log text, not the persisted chat/conversation tables.

    Lines tagged ``[unpersisted]`` are exempt by construction: that tag is
    written only on the branch that skips the persist callback, so such a line
    was shown in the pane and stored nowhere. Showing the user what was read is
    the point; storing it is what the contract forbids."""
    for line in lines:
        if line.endswith("[unpersisted]"):
            continue
        assert fixture not in line, f"fixture leaked into persisted trace line: {line!r}"
    assert fixture not in caplog_text, "fixture leaked into log output"
    if memory is None:
        return
    conn = sqlite3.connect(str(memory._db_path))
    try:
        for table in ("chat_log", "conversation_summaries"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                assert fixture not in str(row), f"fixture leaked into {table}: {row!r}"
    finally:
        conn.close()
