"""Tests for /research plan-search-read-synthesize pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.brain.research import (
    Pick,
    PlannedQuery,
    ResearchRunner,
    ResearchSession,
    ResearchStopReason,
    Source,
    SynthResult,
    Verdict,
    _parse_planner_output,
    _parse_synth_json,
    _render_single_pick,
    _render_synth_result,
    _strip_dangling_markers,
    _validate_picks,
)
from tokenpal.config.schema import CloudSearchConfig
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse
from tokenpal.senses.web_search.client import SearchResult

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ScriptedLLM(AbstractLLMBackend):
    backend_name = "scripted"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__({})
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.call_kwargs: list[dict[str, Any]] = []

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    async def generate(
        self, prompt: str, max_tokens: int = 256, **kwargs: Any
    ) -> LLMResponse:
        self.prompts.append(prompt)
        self.call_kwargs.append(kwargs)
        if not self._responses:
            return LLMResponse(text="", tokens_used=0, model_name="t", latency_ms=0)
        return self._responses.pop(0)

    async def generate_with_tools(self, messages, tools, max_tokens=256, **_: Any):
        raise AssertionError("research path must not use generate_with_tools")


def _ok(text: str, tokens: int = 10) -> LLMResponse:
    return LLMResponse(text=text, tokens_used=tokens, model_name="t", latency_ms=0)


def _hit(url: str, title: str, text: str, backend: str = "duckduckgo") -> SearchResult:
    return SearchResult(query="q", backend=backend, title=title, text=text, source_url=url)  # type: ignore[arg-type]


async def _noop_fetch(_url: str) -> str | None:
    return None


def _logs() -> tuple[list[str], Any]:
    buf: list[str] = []
    return buf, buf.append


# ---------------------------------------------------------------------------
# Planner parsing
# ---------------------------------------------------------------------------


def test_parse_planner_single_query() -> None:
    queries = _parse_planner_output(
        '[{"query": "Apollo 11 moon landing year", "intent": "confirm the year"}]',
        cap=5,
    )
    assert len(queries) == 1
    assert queries[0].query == "Apollo 11 moon landing year"
    assert queries[0].intent == "confirm the year"


def test_parse_planner_multi_query_with_chatter() -> None:
    raw = (
        "Sure, here are the queries:\n"
        '[{"query": "rust vs go perf 2025"}, {"query": "rust vs go ecosystem 2025"}]\n'
        "Hope that helps!"
    )
    queries = _parse_planner_output(raw, cap=5)
    assert [q.query for q in queries] == [
        "rust vs go perf 2025",
        "rust vs go ecosystem 2025",
    ]


def test_parse_planner_caps_at_max() -> None:
    raw = (
        '[{"query": "a"}, {"query": "b"}, {"query": "c"}, '
        '{"query": "d"}, {"query": "e"}]'
    )
    queries = _parse_planner_output(raw, cap=3)
    assert len(queries) == 3


def test_parse_planner_accepts_bare_strings() -> None:
    queries = _parse_planner_output('["one", "two"]', cap=5)
    assert [q.query for q in queries] == ["one", "two"]


def test_parse_planner_falls_back_to_oneliner() -> None:
    queries = _parse_planner_output("just a question string", cap=5)
    assert len(queries) == 1
    assert queries[0].query.startswith("just a question")


def test_parse_planner_returns_empty_for_empty_input() -> None:
    assert _parse_planner_output("", cap=5) == []


# ---------------------------------------------------------------------------
# Citation validation
# ---------------------------------------------------------------------------


def test_strip_dangling_markers_keeps_valid_range() -> None:
    out = _strip_dangling_markers("Fact [1] and fact [2].", max_n=2)
    assert out == "Fact [1] and fact [2]."


def test_strip_dangling_markers_drops_out_of_range() -> None:
    out = _strip_dangling_markers("Real [1] but not [7] nor [99].", max_n=2)
    assert out == "Real [1] but not  nor ."


# ---------------------------------------------------------------------------
# ResearchRunner happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_planner_search_synthesize(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}, {"query": "q2"}]', tokens=50),   # planner
        _ok("Answer summarizing [1] and [2].", tokens=100),     # synthesizer
    ])

    def fake_search_many(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        if backend == "duckduckgo":
            return [_hit(f"https://ddg.example/{q}", "DDG title", "ddg summary", "duckduckgo")]
        if backend == "wikipedia":
            return [_hit(f"https://wiki.example/{q}", "Wiki title", "wiki summary", "wikipedia")]
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        max_queries=2,
        max_fetches=3,
    )
    session = await runner.run("what is X")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(session.queries) == 2
    assert len(session.sources) <= 3
    assert "[1]" in session.answer
    assert session.tokens_used >= 150
    # Log stream records the question, plan queries, and a source line each.
    assert any(line.startswith("?") for line in logs)
    assert any("plan:" in line for line in logs)


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_warns_when_synth_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truncated = LLMResponse(
        text='{"kind": "factual", "answer": "partial',
        tokens_used=1800,
        model_name="t",
        latency_ms=0,
        finish_reason="length",
    )
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=50),  # planner
        truncated,                              # truncated synth
    ])

    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        return [_hit(f"https://ex/{q}", "t", "summary", "duckduckgo")]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, max_fetches=1,
    )
    await runner.run("what is X")
    assert any("synth hit max_tokens" in line for line in logs)


@pytest.mark.asyncio
async def test_runner_no_queries_stops_cleanly() -> None:
    llm = _ScriptedLLM([_ok("", tokens=0)])
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=3
    )
    session = await runner.run("???")
    assert session.stopped_reason == ResearchStopReason.NO_QUERIES
    assert session.sources == []


@pytest.mark.asyncio
async def test_runner_no_sources_stops_before_synthesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _ScriptedLLM([_ok('[{"query": "q1"}]', tokens=30)])

    def empty_search(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", empty_search)

    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb)
    session = await runner.run("unreachable topic")
    assert session.stopped_reason == ResearchStopReason.NO_SOURCES


@pytest.mark.asyncio
async def test_runner_token_budget_skips_search(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _ScriptedLLM([_ok('[{"query": "q1"}]', tokens=9999)])

    def fake_search(q: str, **_: Any) -> list[SearchResult]:
        raise AssertionError("search must not run after token budget trips")

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, token_budget=100
    )
    session = await runner.run("expensive")
    assert session.stopped_reason == ResearchStopReason.TOKEN_BUDGET


@pytest.mark.asyncio
async def test_runner_search_timeout_survives_gather(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One query's search hangs; the other completes. gather(return_exceptions=True)
    should keep the fast one's results."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}, {"query": "q2"}]', tokens=10),
        _ok("The answer [1].", tokens=30),
    ])

    def sometimes_hanging(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        if q == "q1":
            import time as _time
            _time.sleep(0.2)
            return []
        return [_hit(f"https://example/{q}", "T", "snippet", "duckduckgo")]

    monkeypatch.setattr("tokenpal.brain.research.search_many", sometimes_hanging)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        per_search_timeout_s=0.05,
    )
    session = await runner.run("topic")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(session.sources) == 1


# ---------------------------------------------------------------------------
# Source formatting / fetch integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_callback_fires_at_each_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner should push a status label at planning, searching, reading,
    synthesizing, and validating so the overlay can show progress."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=10),
        _ok("Answer [1].", tokens=30),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://example.com", "T", "snip", "duckduckgo")
        ],
    )
    statuses: list[str] = []

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        status_callback=statuses.append,
    )
    session = await runner.run("q")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "researching: planning" in statuses
    assert "researching: searching" in statuses
    assert any(s.startswith("researching: reading ") for s in statuses)
    assert "researching: synthesizing" in statuses
    assert "researching: validating" in statuses


@pytest.mark.asyncio
async def test_fetch_replaces_snippet_with_article_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runner injects raw extracted text — no <tool_result> unwrapping."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=10),
        _ok("Answer [1].", tokens=30),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://example.com", "Title", "short snippet", "duckduckgo")
        ],
    )

    async def fake_fetch(_url: str) -> str:
        return "FULL ARTICLE BODY ABOUT THE TOPIC"

    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=fake_fetch, log_callback=log_cb)
    session = await runner.run("question")

    assert session.sources, "expected at least one source"
    excerpt = session.sources[0].excerpt
    assert "FULL ARTICLE BODY" in excerpt
    assert "short snippet" not in excerpt


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_session_is_complete_helper() -> None:
    assert ResearchSession(
        question="q", stopped_reason=ResearchStopReason.COMPLETE
    ).is_complete is True
    assert ResearchSession(
        question="q", stopped_reason=ResearchStopReason.NO_SOURCES
    ).is_complete is False


def test_planned_query_and_source_shape() -> None:
    q = PlannedQuery(query="x", intent="why")
    assert q.query == "x" and q.intent == "why"
    s = Source(number=1, url="u", title="t", excerpt="e", backend="duckduckgo")
    assert s.number == 1


# ---------------------------------------------------------------------------
# Synth JSON parsing
# ---------------------------------------------------------------------------


def test_parse_synth_json_comparison() -> None:
    raw = (
        '{"kind": "comparison", '
        '"picks": [{"name": "Garmin Forerunner 165", "reason": "battery", "citation": 1}, '
        '{"name": "Fitbit Versa 4", "reason": "iOS app", "citation": 3}], '
        '"verdict": {"text": "Forerunner for training", "citation": 1}}'
    )
    result = _parse_synth_json(raw)
    assert result is not None
    assert result.kind == "comparison"
    assert [p.name for p in result.picks] == ["Garmin Forerunner 165", "Fitbit Versa 4"]
    assert result.verdict is not None
    assert result.verdict.text == "Forerunner for training"


def test_parse_synth_json_factual() -> None:
    raw = '{"kind": "factual", "answer": "Apollo 11 in 1969.", "citations": [1, 2]}'
    result = _parse_synth_json(raw)
    assert result is not None
    assert result.kind == "factual"
    assert result.answer == "Apollo 11 in 1969."
    assert result.citations == [1, 2]


def test_parse_synth_json_tolerates_pre_post_chatter() -> None:
    raw = (
        "Sure, here's the result:\n"
        '{"kind": "factual", "answer": "Because X.", "citations": [1]}\n'
        "Let me know if you need more."
    )
    result = _parse_synth_json(raw)
    assert result is not None
    assert result.kind == "factual"


def test_parse_synth_json_returns_none_for_invalid() -> None:
    assert _parse_synth_json("") is None
    assert _parse_synth_json("not json at all") is None
    assert _parse_synth_json('{"kind": "comparison"}') is not None  # empty picks ok, runner downgrades


def test_parse_synth_json_skips_unrelated_objects() -> None:
    raw = '{"wrong": "shape"} {"kind": "factual", "answer": "A.", "citations": []}'
    result = _parse_synth_json(raw)
    assert result is not None
    assert result.answer == "A."


# ---------------------------------------------------------------------------
# Pick validation
# ---------------------------------------------------------------------------


def _src(number: int, excerpt: str) -> Source:
    return Source(number=number, url=f"u{number}", title="t", excerpt=excerpt)


def test_validate_picks_keeps_names_in_excerpt() -> None:
    sources = [_src(1, "The Garmin Forerunner 165 has 25-day battery.")]
    picks = [Pick(name="Garmin Forerunner 165", reason="battery", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert kept == picks and dropped == []


def test_validate_picks_drops_names_not_in_excerpt() -> None:
    sources = [_src(1, "The Garmin Forerunner 165 has 25-day battery.")]
    picks = [Pick(name="Apple Watch Series 9", reason="fabricated", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert kept == [] and dropped == picks


def test_validate_picks_repairs_unknown_citation() -> None:
    """Out-of-range citation but name is in a real source. Repair citation
    rather than drop (the name is still grounded)."""
    sources = [_src(1, "Garmin Forerunner 165")]
    picks = [Pick(name="Garmin Forerunner 165", reason="x", citation=99)]
    kept, dropped = _validate_picks(picks, sources)
    assert dropped == []
    assert len(kept) == 1 and kept[0].citation == 1


def test_validate_picks_case_insensitive() -> None:
    sources = [_src(1, "garmin forerunner 165 has gps")]
    picks = [Pick(name="GARMIN FORERUNNER 165", reason="gps", citation=1)]
    kept, _ = _validate_picks(picks, sources)
    assert kept == picks


def test_validate_picks_token_fallback_reordered() -> None:
    """Source says 'Versa 4 from Fitbit' but synth names it 'Fitbit Versa 4'.
    Substring fails, token-overlap rescues it."""
    sources = [_src(1, "The Versa 4 from Fitbit ships with GPS and heart rate.")]
    picks = [Pick(name="Fitbit Versa 4", reason="gps", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert kept == picks and dropped == []


def test_validate_picks_token_fallback_rejects_partial() -> None:
    """Missing any token of the name means the pick is still dropped."""
    sources = [_src(1, "The Fitbit Versa ships with a great display.")]
    picks = [Pick(name="Fitbit Versa 4", reason="display", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert kept == [] and dropped == picks


def test_validate_picks_repairs_wrong_citation() -> None:
    """Name is in source 2's excerpt but synth cited source 1. Citation gets
    repaired to 2 rather than dropping the pick."""
    sources = [
        _src(1, "Generic intro paragraph about fitness."),
        _src(2, "The Apple Watch Series 10 ships with ECG and fall detection."),
    ]
    picks = [Pick(name="Apple Watch Series 10", reason="ecg", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert dropped == []
    assert len(kept) == 1
    assert kept[0].citation == 2
    assert kept[0].name == "Apple Watch Series 10"


def test_validate_picks_drops_when_no_source_contains_name() -> None:
    """Pure hallucination: name appears in NO excerpt. Drop, don't repair."""
    sources = [
        _src(1, "Apple Watch Series 10 review."),
        _src(2, "Garmin Forerunner 265 review."),
    ]
    picks = [Pick(name="Made-Up Tracker 9000", reason="fake", citation=1)]
    kept, dropped = _validate_picks(picks, sources)
    assert kept == [] and dropped == picks


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_synth_result_comparison() -> None:
    result = SynthResult(
        kind="comparison",
        picks=[
            Pick(name="A", reason="fast", citation=1),
            Pick(name="B", reason="cheap", citation=2),
        ],
        verdict=Verdict(text="pick A", citation=1),
    )
    rendered = _render_synth_result(result)
    assert "- A: fast [1]" in rendered
    assert "- B: cheap [2]" in rendered
    assert "Verdict: pick A [1]." in rendered


def test_render_synth_result_factual() -> None:
    result = SynthResult(kind="factual", answer="Because X.", citations=[1, 2])
    assert _render_synth_result(result) == "Because X. [1] [2]"


def test_render_synth_result_factual_no_citations() -> None:
    result = SynthResult(kind="factual", answer="Plain answer.")
    assert _render_synth_result(result) == "Plain answer."


# ---------------------------------------------------------------------------
# Runner end-to-end with JSON synth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_json_synth_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    synth_json = (
        '{"kind": "comparison", "picks": ['
        '{"name": "Garmin Forerunner 165", "reason": "25-day battery", "citation": 1}, '
        '{"name": "Fitbit Versa 4", "reason": "iOS app", "citation": 2}], '
        '"verdict": {"text": "Forerunner wins", "citation": 1}}'
    )
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok(synth_json, tokens=200),
    ])

    def fake_search(
        q: str, backend: str = "duckduckgo", limit: int = 5, **_: Any,
    ) -> list[SearchResult]:
        return [
            _hit(
                "https://a.example",
                "Forbes",
                "The Garmin Forerunner 165 has 25-day battery life.",
                "duckduckgo",
            ),
            _hit(
                "https://b.example",
                "PCMag",
                "Fitbit Versa 4 ships the best iOS app in the category.",
                "wikipedia",
            ),
        ]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        max_queries=1,
        max_fetches=3,
    )
    session = await runner.run("best fitness tracker for iPhone 17")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "Garmin Forerunner 165" in session.answer
    assert "Fitbit Versa 4" in session.answer
    assert "Verdict: Forerunner wins [1]." in session.answer
    assert "[1]" in session.answer and "[2]" in session.answer


@pytest.mark.asyncio
async def test_runner_drops_uncited_pick_and_renders_single(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synth fabricates one pick not in sources; runner drops it and
    renders the one verified pick with a 'more context would help'
    caveat instead of the downgrade (so the user sees a real answer)."""
    synth_json = (
        '{"kind": "comparison", "picks": ['
        '{"name": "Real Watch", "reason": "in source", "citation": 1}, '
        '{"name": "Made-Up Watch 9000", "reason": "hallucinated", "citation": 1}], '
        '"verdict": {"text": "Real Watch", "citation": 1}}'
    )
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok(synth_json, tokens=200),
    ])

    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "The Real Watch has features.", "duckduckgo"),
        ],
    )

    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1)
    session = await runner.run("best")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "Real Watch" in session.answer
    assert "more context would help" in session.answer.lower()
    assert any("Made-Up Watch 9000" in line for line in logs)


@pytest.mark.asyncio
async def test_runner_zero_verified_still_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All picks hallucinated; downgrade stays."""
    synth_json = (
        '{"kind": "comparison", "picks": ['
        '{"name": "Fake One", "reason": "x", "citation": 1}, '
        '{"name": "Fake Two", "reason": "x", "citation": 1}], '
        '"verdict": {"text": "Fake One", "citation": 1}}'
    )
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok(synth_json, tokens=200),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "Nothing matches here.", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1)
    session = await runner.run("best")
    assert "enough verifiable picks" in session.answer


def test_render_single_pick_includes_caveat() -> None:
    pick = Pick(name="LG G4 OLED", reason="best for home theater", citation=2)
    rendered = _render_single_pick(pick)
    assert "LG G4 OLED" in rendered
    assert "best for home theater" in rendered
    assert "[2]" in rendered
    assert "more context would help" in rendered.lower()


@pytest.mark.asyncio
async def test_runner_malformed_json_falls_back_to_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When synth returns unparseable text, runner strips dangling markers
    and uses the raw prose as answer."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok("Free-form prose answer [1] with an out-of-range [99].", tokens=100),
    ])

    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet", "duckduckgo"),
        ],
    )

    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1)
    session = await runner.run("anything")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "Free-form prose answer [1]" in session.answer
    assert "[99]" not in session.answer


@pytest.mark.asyncio
async def test_runner_synth_call_requests_thinking_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synth stage must pass enable_thinking=True (default) and the JSON
    schema response_format, so the plumbing carries through the backend."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "A.", "citations": [1]}', tokens=50),
    ])

    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet", "duckduckgo"),
        ],
    )

    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1)
    await runner.run("q")

    synth_kwargs = llm.call_kwargs[1]
    assert synth_kwargs.get("enable_thinking") is True
    fmt = synth_kwargs.get("response_format")
    assert fmt is not None
    assert fmt["type"] == "json_schema"
    assert "schema" in fmt


@pytest.mark.asyncio
async def test_runner_factual_drops_out_of_range_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """factual kind: runner drops citations pointing past the source list."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "Answer.", "citations": [1, 99]}', tokens=50),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1)
    session = await runner.run("q")

    assert "[1]" in session.answer
    assert "[99]" not in session.answer


@pytest.mark.asyncio
async def test_runner_synth_thinking_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "A.", "citations": [1]}', tokens=50),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, synth_thinking=False,
    )
    await runner.run("q")
    assert llm.call_kwargs[1].get("enable_thinking") is False


# ---------------------------------------------------------------------------
# Cloud synth path (Anthropic-backed /research synth stage)
# ---------------------------------------------------------------------------


class _FakeCloud:
    """Stand-in for CloudBackend used in research runner tests."""

    def __init__(self, response: LLMResponse | None = None,
                 raise_on_call: Exception | None = None) -> None:
        self.model = "claude-haiku-4-5"
        self._response = response or LLMResponse(
            text='{"kind": "factual", "answer": "Cloud [1].", "citations": [1]}',
            tokens_used=42,
            model_name="claude-haiku-4-5",
            latency_ms=100.0,
        )
        self._raise = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def synthesize(self, prompt: str, **kwargs: Any) -> LLMResponse:
        self.calls.append({"prompt": prompt, **kwargs})
        if self._raise is not None:
            raise self._raise
        return self._response


@pytest.mark.asyncio
async def test_cloud_backend_handles_synth_and_bypasses_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Local LLM only serves the planner; synth must come from cloud.
    llm = _ScriptedLLM([_ok('[{"query": "q1"}]', tokens=30)])
    cloud = _FakeCloud()

    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, cloud_backend=cloud,
    )
    session = await runner.run("q")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(cloud.calls) == 1, "cloud synth must have been invoked exactly once"
    # Local LLM got planner only — one call total.
    assert len(llm.prompts) == 1
    # A log line flags that cloud path ran.
    assert any("cloud" in line.lower() for line in logs)


@pytest.mark.asyncio
async def test_cloud_backend_failure_falls_back_to_local_synth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenpal.llm.cloud_backend import CloudBackendError

    # Local LLM serves planner AND local synth fallback.
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "Local [1].", "citations": [1]}',
            tokens=50),
    ])
    cloud = _FakeCloud(
        raise_on_call=CloudBackendError("boom", kind="network"),
    )

    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, cloud_backend=cloud,
    )
    session = await runner.run("q")

    # Cloud was tried once, then local synth ran.
    assert len(cloud.calls) == 1
    assert len(llm.prompts) == 2  # planner + local synth fallback
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "Local" in session.answer
    # Log flags the fallback.
    assert any("falling back to local" in line for line in logs)


@pytest.mark.asyncio
async def test_cloud_plan_routes_planner_to_cloud_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_plan=True routes the planner call through the cloud backend.
    Synth stays local here to isolate planner behavior."""
    llm = _ScriptedLLM([
        _ok('{"kind": "factual", "answer": "Ans [1].", "citations": [1]}',
            tokens=50),  # local synth only - planner comes from cloud
    ])
    cloud = _FakeCloud(response=LLMResponse(
        text='[{"query": "q from cloud"}]',
        tokens_used=30, model_name="claude-haiku-4-5", latency_ms=1000.0,
    ))
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, cloud_backend=cloud, cloud_plan=True,
    )
    # _synthesize has its own cloud branch; when cloud is set BOTH planner and
    # synth will try cloud. To keep this test focused on planner, swap synth
    # to the same cloud after planner via a fresh response.
    cloud._response = LLMResponse(
        text='{"kind": "factual", "answer": "Ans [1].", "citations": [1]}',
        tokens_used=50, model_name="claude-haiku-4-5", latency_ms=800.0,
    )
    session = await runner.run("q")
    # Planner + synth both went cloud when cloud_plan=True and backend set.
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(cloud.calls) == 2


@pytest.mark.asyncio
async def test_cloud_plan_false_keeps_planner_local_even_with_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default: cloud_plan=False means planner stays local even when a
    cloud backend is present for synth."""
    llm = _ScriptedLLM([_ok('[{"query": "local planner q"}]', tokens=30)])
    cloud = _FakeCloud()  # synth succeeds via cloud
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, cloud_backend=cloud, cloud_plan=False,
    )
    await runner.run("q")
    # Planner local (1 local call), synth cloud (1 cloud call).
    assert len(llm.prompts) == 1
    assert len(cloud.calls) == 1


@pytest.mark.asyncio
async def test_cloud_plan_failure_falls_back_to_local_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If cloud planner raises, local planner serves the fallback."""
    from tokenpal.llm.cloud_backend import CloudBackendError
    # Local serves: planner fallback, synth fallback (cloud also fails for synth)
    llm = _ScriptedLLM([
        _ok('[{"query": "local q"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "Ans [1].", "citations": [1]}',
            tokens=50),
    ])
    cloud = _FakeCloud(raise_on_call=CloudBackendError("boom", kind="network"))
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=1, cloud_backend=cloud, cloud_plan=True,
    )
    session = await runner.run("q")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    # Cloud tried twice (planner + synth), both failed. Local served both.
    assert len(cloud.calls) == 2
    assert len(llm.prompts) == 2


@pytest.mark.asyncio
async def test_refine_calls_cloud_with_combined_prompt() -> None:
    """refine() builds a prompt containing original question + prior answer
    + follow-up + sources, sends it to cloud, and returns a SynthResult."""
    cloud = _FakeCloud(response=LLMResponse(
        text='{"kind": "factual", "answer": "Refined [1].", "citations": [1]}',
        tokens_used=60, model_name="claude-haiku-4-5", latency_ms=800.0,
    ))
    llm = _ScriptedLLM([])  # refine never calls local
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    sources = [
        Source(number=1, url="https://ex", title="t",
               excerpt="source text about pillows", backend="duckduckgo"),
    ]
    result, raw, tokens = await runner.refine(
        original_question="best pillow",
        prior_answer="Previous answer here.",
        sources=sources,
        follow_up="what about side sleepers?",
    )
    assert result is not None
    assert result.kind == "factual"
    assert tokens == 60
    assert len(cloud.calls) == 1
    prompt = cloud.calls[0]["prompt"]
    assert "best pillow" in prompt  # original question
    assert "side sleepers" in prompt  # follow-up
    assert "Previous answer here." in prompt  # prior answer context
    assert "source text about pillows" in prompt  # sources block


@pytest.mark.asyncio
async def test_refine_without_cloud_backend_raises() -> None:
    """No cloud = no refine. We don't fall back to local for /refine -
    the whole point is using cloud to get a better answer."""
    from tokenpal.llm.cloud_backend import CloudBackendError
    llm = _ScriptedLLM([])
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
    )
    with pytest.raises(CloudBackendError) as exc:
        await runner.refine(
            original_question="q", prior_answer="a",
            sources=[Source(number=1, url="u", title="t", excerpt="e")],
            follow_up="f",
        )
    assert exc.value.kind == "not_configured"


@pytest.mark.asyncio
async def test_no_cloud_backend_uses_local_synth_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: omitting cloud_backend must behave byte-for-byte like before."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=30),
        _ok('{"kind": "factual", "answer": "Local only [1].", "citations": [1]}',
            tokens=50),
    ])
    monkeypatch.setattr(
        "tokenpal.brain.research.search_many",
        lambda q, backend="duckduckgo", limit=5, **_: [
            _hit("https://a.example", "T", "snippet with [1]", "duckduckgo"),
        ],
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb, max_queries=1,
    )
    session = await runner.run("q")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(llm.prompts) == 2
    assert "Local only" in session.answer


# ---------------------------------------------------------------------------
# Deep mode (cloud-native web search)
# ---------------------------------------------------------------------------


class _FakeDeepCloud:
    """Stand-in for CloudBackend.research_deep used in runner tests."""

    def __init__(
        self,
        text: str,
        *,
        tokens_used: int = 500,
        iterations: int = 0,
        raise_on_call: Exception | None = None,
    ) -> None:
        self.model = "claude-sonnet-4-6"
        self._text = text
        self._tokens = tokens_used
        self._iterations = iterations
        self._raise = raise_on_call
        self.calls: list[dict[str, Any]] = []

    def research_deep(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append({"prompt": prompt, **kwargs})
        if self._raise is not None:
            raise self._raise
        from tokenpal.llm.cloud_backend import CloudBackendDeepResult
        return CloudBackendDeepResult(
            text=self._text,
            tokens_used=self._tokens,
            iterations=self._iterations,
            latency_ms=250.0,
            finish_reason="stop",
        )

    # ResearchRunner.run() should NEVER be called in deep mode — if this
    # trips, run_deep leaked into the normal path.
    def synthesize(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover
        raise AssertionError("synthesize must not be called in deep mode")


@pytest.mark.asyncio
async def test_run_deep_bypasses_plan_search_and_synth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Neither the local LLM nor the local search_many should be touched.
    llm = _ScriptedLLM([])

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("search_many must not run in deep mode")
    monkeypatch.setattr("tokenpal.brain.research.search_many", _boom)

    deep_payload = (
        '{"kind":"factual","answer":"Deep answer [1].",'
        '"citations":[1],'
        '"sources":[{"number":1,"url":"https://a.example","title":"A"}]}'
    )
    cloud = _FakeDeepCloud(deep_payload, tokens_used=800, iterations=1)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    session = await runner.run_deep("q")

    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(cloud.calls) == 1
    assert llm.prompts == []
    assert session.tokens_used == 800
    assert len(session.sources) == 1
    assert session.sources[0].url == "https://a.example"
    assert session.sources[0].excerpt == ""  # deep-mode sources are summaries
    assert session.sources[0].backend == "cloud"
    assert "Deep answer" in session.answer
    # Log flags the deep path and its continuation count.
    assert any("deep" in line.lower() for line in logs)


@pytest.mark.asyncio
async def test_run_deep_without_cloud_backend_crashes_cleanly() -> None:
    llm = _ScriptedLLM([])
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
    )
    session = await runner.run_deep("q")
    assert session.stopped_reason == ResearchStopReason.CRASHED


@pytest.mark.asyncio
async def test_run_deep_propagates_cloud_backend_failure() -> None:
    from tokenpal.llm.cloud_backend import CloudBackendError
    llm = _ScriptedLLM([])
    cloud = _FakeDeepCloud(
        "", raise_on_call=CloudBackendError("rate", kind="rate_limit"),
    )
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    session = await runner.run_deep("q")
    assert session.stopped_reason == ResearchStopReason.CRASHED
    # Local synth is NOT attempted on deep-mode failure — the plan calls for
    # surfacing the failure; a caller (slash path) may choose to fall back.
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_run_deep_search_mode_omits_fetch_tool() -> None:
    """mode='search' threads include_fetch=False to the backend."""
    llm = _ScriptedLLM([])
    deep_payload = (
        '{"kind":"factual","answer":"Search-only answer [1].",'
        '"citations":[1],'
        '"sources":[{"number":1,"url":"https://a.example","title":"A"}]}'
    )
    cloud = _FakeDeepCloud(deep_payload, tokens_used=250)
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    session = await runner.run_deep("q", mode="search")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert len(cloud.calls) == 1
    assert cloud.calls[0]["include_fetch"] is False
    assert any("search" in line.lower() for line in logs)


@pytest.mark.asyncio
async def test_run_deep_dedupes_sources_and_remaps_citations() -> None:
    """Sonnet often cites the same URL twice under different numbers. The
    parser must collapse duplicates AND rewrite any pick/verdict citation
    that pointed at a dropped dupe so the rendered answer stays grounded."""
    llm = _ScriptedLLM([])
    # Sources 1 and 3 share a URL; pick cites [3] (the dupe) and should
    # get remapped to [1].
    payload = (
        '{"kind":"comparison",'
        '"picks":['
        '  {"name":"Thing A","reason":"fast","citation":1},'
        '  {"name":"Thing B","reason":"cheap","citation":3}'
        '],'
        '"verdict":{"text":"A wins","citation":3},'
        '"sources":['
        '  {"number":1,"url":"https://a.example","title":"A"},'
        '  {"number":2,"url":"https://b.example","title":"B"},'
        '  {"number":3,"url":"https://a.example","title":"A again"}'
        ']}'
    )
    cloud = _FakeDeepCloud(payload)
    _, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    session = await runner.run_deep("q")
    # Deduped to 2 sources keyed by URL, first occurrence wins.
    assert len(session.sources) == 2
    assert [s.number for s in session.sources] == [1, 2]
    # Pick B and verdict originally cited [3]; both remapped to [1].
    assert "[1]" in session.answer
    assert "[3]" not in session.answer


@pytest.mark.asyncio
async def test_run_deep_comparison_renders_picks_and_verdict() -> None:
    llm = _ScriptedLLM([])
    deep_payload = (
        '{"kind":"comparison",'
        '"picks":['
        '  {"name":"Thing A","reason":"fast","citation":1},'
        '  {"name":"Thing B","reason":"cheap","citation":2}'
        '],'
        '"verdict":{"text":"A wins on speed","citation":1},'
        '"sources":['
        '  {"number":1,"url":"https://a.example","title":"A"},'
        '  {"number":2,"url":"https://b.example","title":"B"}'
        ']}'
    )
    cloud = _FakeDeepCloud(deep_payload)
    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        cloud_backend=cloud,
    )
    session = await runner.run_deep("best X?")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert "Thing A" in session.answer
    assert "Thing B" in session.answer
    assert "Verdict" in session.answer
    assert len(session.sources) == 2


# ---------------------------------------------------------------------------
# Phase 1: cloud_search / Tavily integration
# ---------------------------------------------------------------------------


def _preloaded_hit(
    url: str, title: str, body: str, backend: str = "tavily",
) -> SearchResult:
    """SearchResult with Tavily-style preloaded full body."""
    return SearchResult(
        query="q",
        backend=backend,  # type: ignore[arg-type]
        title=title,
        text=body[:200],  # a short snippet for logging
        source_url=url,
        preloaded_content=body,
    )


@pytest.mark.asyncio
async def test_read_short_circuits_on_preloaded_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tavily-sourced hits must skip the local fetch entirely."""
    fetch_called: list[str] = []

    async def spy_fetch(url: str) -> str | None:
        fetch_called.append(url)
        return "should not be used"

    llm = _ScriptedLLM([
        _ok('[{"query": "q1", "backend": "tavily"}]', tokens=50),
        _ok('{"kind": "factual", "answer": "A [1].", "citations": [1]}', tokens=60),
    ])

    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        if backend == "tavily":
            return [_preloaded_hit(
                "https://tav.example",
                "Tavily article",
                "full extracted body content from tavily " * 50,
            )]
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=spy_fetch,
        log_callback=log_cb,
        max_queries=1,
        max_fetches=1,
        cloud_search=CloudSearchConfig(enabled=True),
        tavily_api_key="tvly-abcdefghijklmnop",
    )
    session = await runner.run("why?")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    # Critical assertion: fetch was NOT invoked because Tavily preloaded content.
    assert fetch_called == []
    assert len(session.sources) == 1
    # Preloaded content flowed through to the Source excerpt.
    assert "tavily" in session.sources[0].excerpt.lower()


@pytest.mark.asyncio
async def test_read_filters_sensitive_preloaded_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sensitive-content filter must still run on Tavily-extracted text."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}]', tokens=50),
        _ok('{"kind": "factual", "answer": "A.", "citations": []}', tokens=60),
    ])

    # Body contains a sensitive-content term that MUST cause the source
    # to be dropped before it reaches the synth prompt.
    bad_body = "this article talks about 1password vaults in detail " * 5

    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        if backend == "tavily":
            return [_preloaded_hit("https://bad", "B", bad_body)]
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        max_queries=1,
        max_fetches=1,
        cloud_search=CloudSearchConfig(enabled=True),
        tavily_api_key="tvly-abcdefghijklmnop",
    )
    session = await runner.run("q")
    # Filter dropped the sensitive source → pipeline reports NO_SOURCES.
    assert session.stopped_reason == ResearchStopReason.NO_SOURCES
    assert session.sources == []


@pytest.mark.asyncio
async def test_thin_tavily_pool_tops_up_from_ddg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tavily returning <3 hits triggers a DDG top-up + transcript warning."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1", "backend": "tavily"}]', tokens=50),
        _ok('{"kind": "factual", "answer": "A [1].", "citations": [1]}', tokens=60),
    ])

    call_log: list[str] = []

    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        call_log.append(backend)
        if backend == "tavily":
            # Only 1 Tavily hit — below _THIN_POOL_THRESHOLD=3.
            return [_preloaded_hit("https://tav", "Tav", "long tavily body " * 30)]
        if backend == "duckduckgo":
            return [
                _hit("https://ddg1", "DDG A", "summary A", "duckduckgo"),
                _hit("https://ddg2", "DDG B", "summary B", "duckduckgo"),
            ]
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        max_queries=1,
        max_fetches=5,
        cloud_search=CloudSearchConfig(enabled=True),
        tavily_api_key="tvly-abcdefghijklmnop",
    )
    session = await runner.run("q?")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    # Tavily was tried, DDG was consulted for top-up.
    assert "tavily" in call_log
    assert "duckduckgo" in call_log
    # Transcript warning surfaced on session (so research_action can render it).
    assert any("tavily thin" in w for w in session.warnings)


@pytest.mark.asyncio
async def test_default_backend_is_ddg_when_cloud_search_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without cloud_search_enabled, dispatch routes to duckduckgo regardless
    of what the planner emits."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1", "backend": "tavily"}]', tokens=50),  # planner tried to pick tavily
        _ok('{"kind": "factual", "answer": "A [1].", "citations": [1]}', tokens=60),
    ])

    call_log: list[str] = []

    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        call_log.append(backend)
        return [_hit("https://ddg", "t", "body", "duckduckgo")]

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm,
        fetch_url=_noop_fetch,
        log_callback=log_cb,
        max_queries=1,
        max_fetches=1,
        cloud_search=CloudSearchConfig(enabled=False),  # disabled
        tavily_api_key="",
    )
    await runner.run("q?")
    # Even though planner said "tavily", dispatch fell back to duckduckgo.
    assert call_log == ["duckduckgo"]


def test_parse_planner_output_carries_backend_field() -> None:
    queries = _parse_planner_output(
        '[{"query": "foo", "backend": "tavily"}, '
        '{"query": "bar", "intent": "b", "backend": "wikipedia"}, '
        '{"query": "baz"}]',
        cap=5,
    )
    assert len(queries) == 3
    assert queries[0].backend == "tavily"
    assert queries[1].backend == "wikipedia"
    assert queries[2].backend == ""  # no backend field → default


@pytest.mark.asyncio
async def test_session_warnings_thin_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing thin-pool log line also appends to session.warnings now,
    so the transcript can surface it (not just Python logs)."""
    llm = _ScriptedLLM([
        _ok('[{"query": "q1"}, {"query": "q2"}]', tokens=50),
        _ok('{"kind": "factual", "answer": "A.", "citations": []}', tokens=60),
    ])

    # Only 1 source returned — triggers thin-pool path.
    def fake_search_many(q, backend="duckduckgo", limit=5, **_):
        if q == "q1":
            return [_hit("https://a", "A", "body", "duckduckgo")]
        return []

    monkeypatch.setattr("tokenpal.brain.research.search_many", fake_search_many)

    logs, log_cb = _logs()
    runner = ResearchRunner(
        llm=llm, fetch_url=_noop_fetch, log_callback=log_cb,
        max_queries=2, max_fetches=5,
    )
    session = await runner.run("q?")
    assert session.stopped_reason == ResearchStopReason.COMPLETE
    assert any("thin source pool" in w for w in session.warnings)
