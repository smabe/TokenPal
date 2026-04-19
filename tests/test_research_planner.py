"""Phase 4 planner routing tests.

These tests verify the contract between:
  1. the planner prompt (tells the LLM *which* backend to pick per query), and
  2. the end-to-end pipeline (_parse_planner_output -> PlannedQuery.backend ->
     _resolve_backend -> search_many dispatch)

We don't assert that a real LLM picks the "right" backend for each canonical
question - that's a model-quality concern and would make this suite flaky.
Instead, we script LLM outputs that represent a well-tuned planner and verify
that the routing layer carries those choices through correctly, including
fall-backs for misconfigured / unknown / typo'd backend names.
"""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.brain.research import (
    _PLANNER_PROMPT,
    ResearchRunner,
    _parse_planner_output,
)
from tokenpal.config.schema import CloudSearchConfig
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
        raise AssertionError("planner path must not use generate_with_tools")


def _ok(text: str) -> LLMResponse:
    return LLMResponse(text=text, tokens_used=10, model_name="t", latency_ms=0)


async def _noop_fetch(_url: str) -> str | None:
    return None


# ---------------------------------------------------------------------------
# Prompt-level sanity checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword",
    ["stackexchange", "hn", "tavily", "brave", "backend"],
)
def test_planner_prompt_advertises_routing_backends(keyword: str) -> None:
    """Smoke test: Phase 4 routing hints made it into the prompt."""
    assert keyword in _PLANNER_PROMPT


def test_planner_prompt_marks_backend_field_optional() -> None:
    assert "optional" in _PLANNER_PROMPT.lower()


# ---------------------------------------------------------------------------
# Golden queries: scripted LLM outputs flow through the parser
# ---------------------------------------------------------------------------

_GOLDEN_CASES: list[tuple[str, str, list[str]]] = [
    (
        "tech how-to",
        '[{"query": "how to parse JSON in python", "backend": "stackexchange"}]',
        ["stackexchange"],
    ),
    (
        "HN launch discussion",
        '[{"query": "Zed editor launch discussion", "backend": "hn"}]',
        ["hn"],
    ),
    (
        "product comparison",
        '[{"query": "best mechanical keyboard 2026 review", "backend": "tavily"},'
        ' {"query": "mechanical keyboard switch comparison 2026", "backend": "tavily"}]',
        ["tavily", "tavily"],
    ),
    (
        "general factual - no backend (runtime default)",
        '[{"query": "Apollo 11 moon landing year"}]',
        [""],
    ),
    (
        "mixed multi-hop",
        '[{"query": "Rust vs Go performance", "backend": "stackexchange"},'
        ' {"query": "Rust vs Go community sentiment", "backend": "hn"}]',
        ["stackexchange", "hn"],
    ),
    (
        "brave alternative",
        '[{"query": "privacy-respecting search engines", "backend": "brave"}]',
        ["brave"],
    ),
    (
        "casing normalized to lowercase",
        '[{"query": "q", "backend": "TAVILY"}]',
        ["tavily"],
    ),
    (
        "explicit ddg",
        '[{"query": "history of the printing press", "backend": "ddg"}]',
        ["ddg"],
    ),
]


@pytest.mark.parametrize("style,raw,expected_backends", _GOLDEN_CASES)
def test_parse_carries_backend_through_to_planned_query(
    style: str, raw: str, expected_backends: list[str]
) -> None:
    queries = _parse_planner_output(raw, cap=5)
    assert [q.backend for q in queries] == expected_backends, style


# ---------------------------------------------------------------------------
# _resolve_backend: runtime normalization + fallback
# ---------------------------------------------------------------------------


def _runner(*, cloud_search_enabled: bool = False, tavily_key: str = "") -> ResearchRunner:
    """Minimal ResearchRunner for _resolve_backend inspection."""
    return ResearchRunner(
        llm=_ScriptedLLM([]),
        fetch_url=_noop_fetch,
        log_callback=lambda _: None,
        cloud_search=CloudSearchConfig(enabled=cloud_search_enabled),
        tavily_api_key=tavily_key,
    )


def test_resolve_backend_empty_defaults_to_duckduckgo_when_cloud_off() -> None:
    r = _runner(cloud_search_enabled=False)
    assert r._resolve_backend("") == "duckduckgo"


def test_resolve_backend_empty_defaults_to_tavily_when_cloud_active() -> None:
    r = _runner(cloud_search_enabled=True, tavily_key="tvly-x")
    assert r._resolve_backend("") == "tavily"


def test_resolve_backend_tavily_downgraded_without_key() -> None:
    """Planner can still emit "tavily" even if the user never enabled it —
    we quietly fall back to DDG instead of trying to hit Tavily with no key."""
    r = _runner(cloud_search_enabled=False)
    assert r._resolve_backend("tavily") == "duckduckgo"


def test_resolve_backend_routes_hn_stackexchange_brave() -> None:
    """Keyless Phase 3 backends + Brave pass through untouched."""
    r = _runner()
    assert r._resolve_backend("hn") == "hn"
    assert r._resolve_backend("stackexchange") == "stackexchange"
    assert r._resolve_backend("brave") == "brave"


def test_resolve_backend_unknown_falls_back_to_default() -> None:
    """LLM hallucinations (bing, google, typos) shouldn't crash dispatch."""
    r = _runner()
    assert r._resolve_backend("bing") == "duckduckgo"
    assert r._resolve_backend("googel") == "duckduckgo"
    assert r._resolve_backend("  ") == "duckduckgo"


def test_resolve_backend_case_insensitive() -> None:
    r = _runner()
    assert r._resolve_backend("STACKEXCHANGE") == "stackexchange"
    assert r._resolve_backend("  HN  ") == "hn"


def test_resolve_backend_ddg_alias_for_duckduckgo() -> None:
    """The planner prompt uses "ddg" shorthand; accept it as DuckDuckGo
    even when cloud_search is on (where the runtime default would otherwise
    be tavily)."""
    r = _runner(cloud_search_enabled=True, tavily_key="tvly-x")
    assert r._resolve_backend("ddg") == "duckduckgo"
    assert r._resolve_backend("DDG") == "duckduckgo"


# ---------------------------------------------------------------------------
# End-to-end: planner LLM output -> session.queries carries backend choices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_emits_end_of_run_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 5 telemetry: a one-line summary lands in the session log so we
    can measure post-ship backend mix and judge whether Playwright is worth
    adding."""
    llm = _ScriptedLLM([
        _ok(
            '[{"query": "a", "backend": "stackexchange"},'
            ' {"query": "b", "backend": "hn"}]'
        ),
        _ok('{"kind": "factual", "answer": "Summary.", "citations": []}'),
    ])

    def fake_search_many(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        return [SearchResult(
            query=q, backend=backend,  # type: ignore[arg-type]
            title="t", text="body", source_url=f"https://ex/{q}",
        )]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs: list[str] = []
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=logs.append,
        max_queries=2,
        max_fetches=3,
    )
    await runner.run("mixed")

    telemetry = [ln for ln in logs if "telemetry:" in ln]
    assert len(telemetry) == 1
    line = telemetry[0]
    assert "mode=" in line
    assert "hn=" in line and "stackexchange=" in line
    assert "sources=2" in line
    assert "stopped=" in line


@pytest.mark.asyncio
async def test_runner_telemetry_fires_on_no_queries() -> None:
    """Telemetry fires even when the run exits early; empty mix is reported
    as `mode=none` so analysis scripts can still count the run."""
    llm = _ScriptedLLM([_ok("")])
    logs: list[str] = []
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=logs.append,
        max_queries=2,
    )
    await runner.run("?")

    telemetry = [ln for ln in logs if "telemetry:" in ln]
    assert len(telemetry) == 1
    assert "mode=none" in telemetry[0]
    assert "sources=0" in telemetry[0]


@pytest.mark.asyncio
async def test_runner_preserves_backend_routing_through_plan_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM emits mixed backend routing, runner preserves it on session.queries,
    and search_many is dispatched with the resolved backend for each query."""
    llm = _ScriptedLLM([
        _ok(
            '[{"query": "how to parse json in python", "backend": "stackexchange"},'
            ' {"query": "zed editor launch buzz", "backend": "hn"},'
            ' {"query": "history of turing machines"}]'
        ),
        _ok('{"kind": "factual", "answer": "Summary.", "citations": []}'),
    ])

    seen_backends: list[str] = []

    def fake_search_many(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        seen_backends.append(backend)
        return [SearchResult(
            query=q, backend=backend,  # type: ignore[arg-type]
            title="t", text="body", source_url=f"https://ex/{q}",
        )]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=lambda _: None,
        max_queries=3,
        max_fetches=3,
    )
    session = await runner.run("mixed routing")

    assert [q.backend for q in session.queries] == ["stackexchange", "hn", ""]
    # First two queries route as planned; third falls back to the runtime
    # default (DDG, since cloud_search is off in this runner).
    assert sorted(seen_backends) == sorted(["stackexchange", "hn", "duckduckgo"])
