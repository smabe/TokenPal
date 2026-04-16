"""Tests for the research action."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.actions.research.research_action import ResearchAction
from tokenpal.config.schema import ResearchConfig
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse
from tokenpal.senses.web_search.client import SearchResult


class _ScriptedLLM(AbstractLLMBackend):
    backend_name = "scripted"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__({})
        self._responses = list(responses)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    async def generate(
        self, prompt: str, max_tokens: int = 256, **_: Any
    ) -> LLMResponse:
        if not self._responses:
            return LLMResponse(text="", tokens_used=0, model_name="t", latency_ms=0)
        return self._responses.pop(0)

    async def generate_with_tools(self, messages, tools, max_tokens=256, **_: Any):
        raise AssertionError("research path must not use generate_with_tools")


def _ok(text: str, tokens: int = 10) -> LLMResponse:
    return LLMResponse(text=text, tokens_used=tokens, model_name="t", latency_ms=0)


@pytest.fixture()
def grant_all_consent(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)
    consent_mod.save_consent(
        {
            consent_mod.Category.WEB_FETCHES: True,
            consent_mod.Category.RESEARCH_MODE: True,
        },
        path,
    )
    yield


@pytest.mark.asyncio
async def test_rejects_empty_question() -> None:
    action = ResearchAction({})
    result = await action.execute(question="")
    assert result.success is False
    assert "empty question" in result.output


@pytest.mark.asyncio
async def test_without_research_consent_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)

    action = ResearchAction({})
    action._llm = _ScriptedLLM([])
    result = await action.execute(question="anything")
    assert result.success is False
    assert "research_mode" in result.output


@pytest.mark.asyncio
async def test_without_web_fetches_consent_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)
    consent_mod.save_consent(
        {consent_mod.Category.RESEARCH_MODE: True}, path,
    )

    action = ResearchAction({})
    action._llm = _ScriptedLLM([])
    result = await action.execute(question="anything")
    assert result.success is False
    assert "web_fetches" in result.output


@pytest.mark.asyncio
async def test_llm_not_injected_errors(grant_all_consent) -> None:
    action = ResearchAction({})
    result = await action.execute(question="anything")
    assert result.success is False
    assert "not wired" in result.output


@pytest.mark.asyncio
async def test_happy_path_returns_cited_answer(
    monkeypatch: pytest.MonkeyPatch, grant_all_consent,
) -> None:
    def fake_search(
        query: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                query=query,
                backend=backend,  # type: ignore[arg-type]
                title=f"Title for {query}",
                text=f"Snippet about {query}",
                source_url=f"https://example.com/{backend}",
            )
        ]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search)

    async def fake_fetch(url: str, **_: Any) -> str | None:
        return f"Full article text from {url}"

    monkeypatch.setattr(
        "tokenpal.actions.research.research_action.fetch_and_extract", fake_fetch,
    )

    llm = _ScriptedLLM([
        _ok('[{"query": "test query", "intent": "find facts"}]'),
        _ok("The answer with citation [1] and another [2]."),
    ])

    action = ResearchAction({})
    action._llm = llm
    action._research_config = ResearchConfig(max_queries=1, max_fetches=2)

    result = await action.execute(question="what is x?")
    assert result.success is True
    assert "<tool_result" in result.output
    assert "<answer>" in result.output
    assert "<sources>" in result.output
    assert "[1]" in result.output
    assert "https://example.com/" in result.output
    assert result.display_urls is not None
    assert len(result.display_urls) >= 1
    assert all(u.startswith("https://") for _, u in result.display_urls)
    assert any(label.startswith("[1]") for label, _ in result.display_urls)


@pytest.mark.asyncio
async def test_failed_pipeline_returns_failure(
    monkeypatch: pytest.MonkeyPatch, grant_all_consent,
) -> None:
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many", lambda *_a, **_kw: [],
    )

    llm = _ScriptedLLM([
        _ok('[{"query": "test", "intent": "find"}]'),
    ])

    action = ResearchAction({})
    action._llm = llm
    action._research_config = ResearchConfig(max_queries=1)

    result = await action.execute(question="unknowable thing")
    assert result.success is False
    assert "incomplete" in result.output
