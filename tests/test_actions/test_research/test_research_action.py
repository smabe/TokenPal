"""Tests for the research action."""

from __future__ import annotations

from typing import Any

import pytest

from tests._helpers import ScriptedLLM
from tests._helpers import ok_response as _ok
from tokenpal.actions.research.research_action import ResearchAction
from tokenpal.config.schema import ResearchConfig
from tokenpal.llm.base import LLMResponse
from tokenpal.senses.web_search.client import SearchResult


def _ScriptedLLM(responses: list[LLMResponse]) -> ScriptedLLM:  # noqa: N802
    """Research action path must never reach for tool-calling."""
    return ScriptedLLM(responses, forbid_tools=True)


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


def test_format_result_renders_warnings_xml() -> None:
    """Session warnings must surface in <warnings> block so the pal's
    transcript (which renders <tool_result> XML) shows degraded-coverage
    signals to the user, not just to log files."""
    from tokenpal.actions.research.research_action import _format_result
    from tokenpal.brain.research import ResearchSession, Source

    sess = ResearchSession(
        question="x",
        sources=[Source(number=1, url="https://u", title="t", excerpt="body")],
        answer="Answer [1].",
        warnings=["tavily thin (1 sources) — topped up from ddg"],
    )
    out = _format_result(sess)
    assert "<warnings>" in out
    assert "<warning>tavily thin (1 sources) — topped up from ddg</warning>" in out
    assert "</warnings>" in out
    assert "<answer>" in out


def test_format_result_omits_warnings_block_when_clean() -> None:
    from tokenpal.actions.research.research_action import _format_result
    from tokenpal.brain.research import ResearchSession, Source

    sess = ResearchSession(
        question="x",
        sources=[Source(number=1, url="https://u", title="t", excerpt="body")],
        answer="Answer [1].",
    )
    out = _format_result(sess)
    assert "<warnings>" not in out
