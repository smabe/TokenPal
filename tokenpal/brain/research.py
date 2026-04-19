"""Research mode for /research <question>.

Plan → parallel search → fetch → synthesize. Each stage is independently
testable; the runner is framework-agnostic so tests inject mock search
and fetch functions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from tokenpal.brain.stop_reason import ResearchStopReason
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.cloud_backend import CloudBackend, CloudBackendError
from tokenpal.senses.web_search.client import (
    BackendName,
    SearchResult,
    search_many,
)

log = logging.getLogger(__name__)

LogFn = Callable[[str], None]
FetchFn = Callable[[str], "asyncio.Future[str | None]"] | Callable[[str], Any]


@dataclass
class PlannedQuery:
    query: str
    intent: str = ""


@dataclass
class Source:
    number: int
    url: str
    title: str
    excerpt: str
    backend: str = ""


@dataclass
class Pick:
    name: str
    reason: str
    citation: int


@dataclass
class Verdict:
    text: str
    citation: int


@dataclass
class SynthResult:
    """Structured output from the synthesizer. Rendered to session.answer
    by the runner after citation-substring validation."""

    kind: Literal["comparison", "factual"]
    picks: list[Pick] = field(default_factory=list)
    verdict: Verdict | None = None
    answer: str = ""
    citations: list[int] = field(default_factory=list)


@dataclass
class ResearchSession:
    question: str
    queries: list[PlannedQuery] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    answer: str = ""
    tokens_used: int = 0
    stopped_reason: ResearchStopReason | str = ""
    started_at: float = field(default_factory=time.monotonic)

    @property
    def is_complete(self) -> bool:
        return self.stopped_reason == ResearchStopReason.COMPLETE


# Per-backend rate limits (requests in flight). DuckDuckGo HTML endpoint
# 429s at ~20/min per IP; the Instant Answer endpoint used here is laxer
# but still not free-for-all. Wikipedia REST handles 200/s easily.
_BACKEND_CONCURRENCY: dict[BackendName, int] = {
    "duckduckgo": 2,
    "wikipedia": 5,
    "brave": 1,
}

# Per-source excerpt cap handed to the synthesizer. Bigger = better picks
# list on roundup pages (first ~2K is often intro fluff); capped to keep
# token usage bounded across max_fetches sources.
_PER_SOURCE_EXCERPT_CHARS = 4000

# Fewer sources than this and we warn — synthesis from 1-2 pages tends to
# produce either training-data hallucinations or single-source bias.
_THIN_POOL_THRESHOLD = 3


class ResearchRunner:
    """Runs a single /research question end-to-end.

    The runner is framework-agnostic: all LLM calls go through the backend
    interface, and fetch_url is injected as a callable so tests can substitute
    a mock without touching the network or aiohttp.
    """

    def __init__(
        self,
        llm: AbstractLLMBackend,
        fetch_url: Callable[[str], Any],
        *,
        log_callback: LogFn,
        status_callback: Callable[[str], None] | None = None,
        max_queries: int = 3,
        max_fetches: int = 5,
        token_budget: int = 6000,
        per_search_timeout_s: float = 5.0,
        per_fetch_timeout_s: float = 8.0,
        synth_thinking: bool = True,
        cloud_backend: CloudBackend | None = None,
        cloud_plan: bool = False,
    ) -> None:
        self._llm = llm
        self._fetch = fetch_url
        self._log = log_callback
        self._status = status_callback
        self._max_queries = max_queries
        self._max_fetches = max_fetches
        self._token_budget = token_budget
        self._per_search_timeout_s = per_search_timeout_s
        self._per_fetch_timeout_s = per_fetch_timeout_s
        self._synth_thinking = synth_thinking
        self._cloud_backend = cloud_backend
        self._cloud_plan = cloud_plan
        self._semaphores: dict[BackendName, asyncio.Semaphore] = {
            name: asyncio.Semaphore(limit)
            for name, limit in _BACKEND_CONCURRENCY.items()
        }

    def _set_status(self, label: str) -> None:
        if self._status is None:
            return
        try:
            self._status(label)
        except Exception:
            log.exception("research status_callback raised")

    async def run(self, question: str) -> ResearchSession:
        session = ResearchSession(question=question)
        self._log(f"? {question}")
        self._set_status("researching: planning")

        try:
            session.queries = await self._plan(question, session)
        except Exception:
            log.exception("Research planner failed")
            session.stopped_reason = ResearchStopReason.CRASHED
            return session

        if not session.queries:
            session.stopped_reason = ResearchStopReason.NO_QUERIES
            return session

        for q in session.queries:
            self._log(f"  plan: {q.query}")

        if session.tokens_used >= self._token_budget:
            session.stopped_reason = ResearchStopReason.TOKEN_BUDGET
            return session

        self._set_status("researching: searching")
        hits = await self._search_all(session.queries)
        if not hits:
            session.stopped_reason = ResearchStopReason.NO_SOURCES
            return session

        capped = hits[: self._max_fetches]
        self._set_status(f"researching: reading 0/{len(capped)}")
        session.sources = await self._read_all(capped)
        for src in session.sources:
            self._log(f"  [{src.number}] {src.url}")

        if not session.sources:
            session.stopped_reason = ResearchStopReason.NO_SOURCES
            return session

        if len(session.sources) < _THIN_POOL_THRESHOLD:
            self._log(
                f"  warning: thin source pool ({len(session.sources)} sources) "
                f"— answer may be unreliable"
            )
            log.warning(
                "Research returned %d sources (threshold %d) — synthesis "
                "will be thin", len(session.sources), _THIN_POOL_THRESHOLD,
            )

        self._set_status("researching: synthesizing")
        try:
            result, raw_text, used = await self._synthesize(question, session.sources)
        except Exception:
            log.exception("Research synthesizer failed")
            session.stopped_reason = ResearchStopReason.CRASHED
            return session

        session.tokens_used += used
        self._set_status("researching: validating")
        session.answer = self._finalize_answer(result, raw_text, session.sources)
        session.stopped_reason = ResearchStopReason.COMPLETE
        return session

    def _finalize_answer(
        self,
        result: SynthResult | None,
        raw_text: str,
        sources: list[Source],
    ) -> str:
        max_n = len(sources)
        if result is None:
            self._log(
                "  synth: JSON parse failed, falling back to prose + marker strip"
            )
            log.warning("research synth returned invalid JSON, using prose fallback")
            all_markers = _DANGLING_MARKER_RE.findall(raw_text)
            stripped_text = _strip_dangling_markers(raw_text, max_n)
            stripped_count = sum(1 for n in all_markers if not 1 <= int(n) <= max_n)
            if stripped_count:
                kept_count = len(all_markers) - stripped_count
                self._log(
                    f"  citations: {kept_count} kept, {stripped_count} stripped "
                    f"(out-of-range, possible hallucination)"
                )
            return stripped_text

        if result.kind == "comparison":
            kept, dropped = _validate_picks(result.picks, sources)
            if dropped:
                names = "; ".join(p.name for p in dropped)
                self._log(
                    f"  picks: {len(result.picks)} generated, "
                    f"{len(dropped)} dropped (not in any source): {names}"
                )
                log.info(
                    "research dropped %d of %d picks: %s",
                    len(dropped), len(result.picks), names,
                )
            if len(kept) == 0:
                self._log(
                    f"  synth: {len(result.picks)} picks generated, "
                    f"0 verified, downgrading"
                )
                log.info(
                    "research synth produced %d picks, 0 verified "
                    "(raw len=%d)",
                    len(result.picks), len(raw_text),
                )
                return "Sources don't name enough verifiable picks."
            if len(kept) == 1:
                self._log(
                    f"  synth: {len(result.picks)} picks generated, "
                    f"only 1 verified, rendering with caveat"
                )
                log.info(
                    "research synth produced %d picks, 1 verified "
                    "(raw len=%d)",
                    len(result.picks), len(raw_text),
                )
                return _render_single_pick(kept[0])
            return _render_synth_result(replace(result, picks=kept))

        valid_citations = [c for c in result.citations if 1 <= c <= max_n]
        return _render_synth_result(replace(result, citations=valid_citations))

    # ---- Stage 1: planner -------------------------------------------------

    async def _plan(self, question: str, session: ResearchSession) -> list[PlannedQuery]:
        from datetime import datetime
        prompt = _PLANNER_PROMPT.format(
            question=question,
            max_queries=self._max_queries,
            current_year=datetime.now().year,
        )
        if self._cloud_backend is not None and self._cloud_plan:
            log.info("research plan: dispatching to cloud (%s)",
                     self._cloud_backend.model)
            try:
                response = await asyncio.to_thread(
                    self._cloud_backend.synthesize,
                    prompt,
                    max_tokens=400,
                    json_schema=None,  # planner output is a tolerant JSON array
                )
                log.info("research plan: cloud returned %d tokens in %.1fs",
                         response.tokens_used, response.latency_ms / 1000.0)
            except CloudBackendError as e:
                log.warning("cloud plan failed (%s): %s - using local", e.kind, e)
                response = await self._llm.generate(prompt, max_tokens=400)
        else:
            response = await self._llm.generate(prompt, max_tokens=400)
        session.tokens_used += response.tokens_used
        return _parse_planner_output(response.text, self._max_queries)

    # ---- Stage 2: search --------------------------------------------------

    async def _search_all(
        self, queries: list[PlannedQuery]
    ) -> list[SearchResult]:
        # Wikipedia's summary endpoint needs exact article titles, but
        # planner queries are search-engine phrasings ("best X for Y 2026"),
        # never article slugs, so every Wikipedia call 404s. /ask still uses
        # Wikipedia for factual one-shot lookups where the query IS a title.
        tasks = [self._search_many(q.query, "duckduckgo") for q in queries]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        collected: list[SearchResult] = []
        seen: set[str] = set()
        for batch in batches:
            if isinstance(batch, Exception):
                log.debug("search sub-task failed: %s", batch)
                continue
            for hit in batch:
                if hit.source_url and hit.source_url not in seen:
                    collected.append(hit)
                    seen.add(hit.source_url)
        return collected

    async def _search_many(
        self, query: str, backend: BackendName, limit: int = 5,
    ) -> list[SearchResult]:
        sem = self._semaphores.get(backend)
        if sem is None:
            return []
        async with sem:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(search_many, query, backend, limit),
                    timeout=self._per_search_timeout_s,
                )
            except TimeoutError:
                log.debug("search_many timeout: %s (%s)", query, backend)
                return []
            except Exception:
                log.exception("search_many backend %s crashed", backend)
                return []

    # ---- Stage 3: read ----------------------------------------------------

    async def _read_all(self, hits: list[SearchResult]) -> list[Source]:
        """Fan fetches out in parallel with bounded concurrency so one slow
        host can't stall the pipeline. Source numbers match hit order."""
        sem = asyncio.Semaphore(3)
        total = len(hits)
        done = 0

        async def _one(i: int, hit: SearchResult) -> Source | None:
            nonlocal done
            async with sem:
                src = await self._read(i, hit)
            done += 1
            self._set_status(f"researching: reading {done}/{total}")
            return src

        results = await asyncio.gather(
            *(_one(i, h) for i, h in enumerate(hits, start=1))
        )
        return [s for s in results if s is not None]

    async def _read(self, number: int, hit: SearchResult) -> Source | None:
        """Prefer the search snippet; optionally enrich with fetched article."""
        excerpt = (hit.text or "").strip()
        url = hit.source_url

        if url and self._fetch is not None:
            try:
                fetched = await asyncio.wait_for(
                    self._fetch(url), timeout=self._per_fetch_timeout_s
                )
            except TimeoutError:
                fetched = None
            except Exception:
                log.exception("fetch raised for %s", url)
                fetched = None
            if fetched:
                excerpt = str(fetched)[:_PER_SOURCE_EXCERPT_CHARS]

        if not excerpt:
            return None
        return Source(
            number=number,
            url=url,
            title=hit.title,
            excerpt=excerpt,
            backend=hit.backend,
        )

    # ---- Stage 4: synthesizer --------------------------------------------

    async def _synthesize(
        self, question: str, sources: list[Source]
    ) -> tuple[SynthResult | None, str, int]:
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "research synth input: excerpt chars per source = %s",
                {s.number: len(s.excerpt) for s in sources},
            )
        sources_block = "\n\n".join(
            f"[{s.number}] {s.url}\n{s.excerpt}" for s in sources
        )
        marker_range = f"[1]..[{len(sources)}]"
        prompt = _SYNTH_PROMPT.format(
            sources_block=sources_block,
            question=question,
            marker_range=marker_range,
        )
        # Thinking can burn ~900 tokens before the JSON starts; llama-server
        # counts reasoning tokens against max_tokens, so give the synth a
        # bigger budget to avoid truncating the picks list. On the cloud path
        # Sonnet/Opus adaptive-thinking tokens also count against max_tokens,
        # so we give cloud a larger ceiling - typical Haiku output is
        # ~300 tokens, but Sonnet with thinking can use 1-2K on hard syntheses.
        budget = 1800 if self._synth_thinking else 700
        cloud_budget = 4000

        if self._cloud_backend is not None:
            log.info("research synth: dispatching to cloud (%s)",
                     self._cloud_backend.model)
            try:
                response = await asyncio.to_thread(
                    self._cloud_backend.synthesize,
                    prompt,
                    max_tokens=cloud_budget,
                    json_schema=SYNTH_SCHEMA,
                )
                self._log(f"  synth: cloud ({self._cloud_backend.model})")
                log.info("research synth: cloud returned %d tokens in %.1fs",
                         response.tokens_used, response.latency_ms / 1000.0)
            except CloudBackendError as e:
                self._log(f"  synth: cloud failed ({e.kind}), falling back to local")
                log.warning("cloud synth failed (%s): %s", e.kind, e)
                response = await self._llm.generate(
                    prompt,
                    max_tokens=budget,
                    enable_thinking=self._synth_thinking,
                    response_format={"type": "json_schema", "schema": SYNTH_SCHEMA},
                )
        else:
            log.info("research synth: local (no cloud backend)")
            response = await self._llm.generate(
                prompt,
                max_tokens=budget,
                enable_thinking=self._synth_thinking,
                response_format={"type": "json_schema", "schema": SYNTH_SCHEMA},
            )
        raw_text = response.text.strip()
        log.debug(
            "research synth: %d chars, finish=%s, tokens=%d",
            len(raw_text), response.finish_reason, response.tokens_used,
        )
        if response.finish_reason == "length":
            self._log(
                f"  warning: synth hit max_tokens ({budget}), JSON may be truncated"
            )
            log.warning(
                "research synth truncated at max_tokens=%d (tokens_used=%d); "
                "parse may fall back to prose path",
                budget, response.tokens_used,
            )
        result = _parse_synth_json(raw_text)
        return result, raw_text, response.tokens_used

    # ---- Refine ----------------------------------------------------------

    async def refine(
        self,
        original_question: str,
        prior_answer: str,
        sources: list[Source],
        follow_up: str,
    ) -> tuple[SynthResult | None, str, int]:
        """Re-synthesize against cached sources with a user follow-up.

        Requires a cloud backend - the whole point of /refine is to get a
        smarter re-analysis than local can manage. Returns the same shape
        as _synthesize so the renderer handles both identically.
        """
        if self._cloud_backend is None:
            raise CloudBackendError(
                "refine requires /cloud enable (no cloud backend configured)",
                kind="not_configured",
            )
        sources_block = "\n\n".join(
            f"[{s.number}] {s.url}\n{s.excerpt}" for s in sources
        )
        marker_range = f"[1]..[{len(sources)}]"
        prompt = _REFINE_PROMPT.format(
            sources_block=sources_block,
            original_question=original_question,
            prior_answer=prior_answer,
            follow_up=follow_up,
            marker_range=marker_range,
        )
        log.info("research refine: dispatching to cloud (%s)",
                 self._cloud_backend.model)
        response = await asyncio.to_thread(
            self._cloud_backend.synthesize,
            prompt,
            max_tokens=4000,
            json_schema=SYNTH_SCHEMA,
        )
        log.info("research refine: cloud returned %d tokens in %.1fs",
                 response.tokens_used, response.latency_ms / 1000.0)
        raw_text = response.text.strip()
        if response.finish_reason == "length":
            log.warning("research refine truncated at max_tokens=4000")
        result = _parse_synth_json(raw_text)
        return result, raw_text, response.tokens_used


_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", re.DOTALL)


def _parse_planner_output(text: str, cap: int) -> list[PlannedQuery]:
    """Planner emits a JSON array. Tries every bracketed span in the text
    until one parses to a list — greedy `\\[.*\\]` would span prose like
    ``Here's [a note]. Plan: [{...}]`` and capture invalid JSON."""
    if not text:
        return []
    for match in _JSON_ARRAY_RE.finditer(text):
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        queries: list[PlannedQuery] = []
        for item in parsed[:cap]:
            if isinstance(item, dict):
                q = (item.get("query") or item.get("q") or "").strip()
                intent = (item.get("intent") or "").strip()
                if q:
                    queries.append(PlannedQuery(query=q, intent=intent))
            elif isinstance(item, str):
                s = item.strip()
                if s:
                    queries.append(PlannedQuery(query=s))
        if queries:
            return queries
    bare = text.strip().strip('"').strip()
    if bare:
        return [PlannedQuery(query=bare[:200])]
    return []


_DANGLING_MARKER_RE = re.compile(r"\[(\d+)\]")


def _strip_dangling_markers(text: str, max_n: int) -> str:
    def _repl(m: re.Match[str]) -> str:
        try:
            n = int(m.group(1))
        except ValueError:
            return ""
        return m.group(0) if 1 <= n <= max_n else ""

    return _DANGLING_MARKER_RE.sub(_repl, text)


# JSON schema for the synth response. Sent as response_format on
# grammar-constrained backends (llama-server) and as a hint on others
# (Ollama). The parser validates shape regardless of backend honoring.
SYNTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["comparison", "factual"]},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                    "citation": {"type": "integer"},
                },
                "required": ["name", "reason", "citation"],
            },
        },
        "verdict": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "citation": {"type": "integer"},
            },
            "required": ["text", "citation"],
        },
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["kind"],
}


def _parse_synth_json(text: str) -> SynthResult | None:
    """Scan for the first valid top-level JSON object that matches the synth
    shape. Uses ``raw_decode`` so nested objects inside ``picks``/``verdict``
    work without a custom balanced-brace regex. Returns None on parse failure
    so the runner can fall back to the prose path."""
    if not text:
        return None
    decoder = json.JSONDecoder()
    cursor = 0
    while True:
        start = text.find("{", cursor)
        if start == -1:
            return None
        try:
            parsed, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        cursor = start + 1
        if not isinstance(parsed, dict):
            continue
        result = _build_synth_result(parsed)
        if result is not None:
            return result


def _build_synth_result(parsed: dict[str, Any]) -> SynthResult | None:
    kind = parsed.get("kind")
    if kind == "comparison":
        picks = [
            Pick(
                name=str(item["name"]),
                reason=str(item["reason"]),
                citation=int(item["citation"]),
            )
            for item in parsed.get("picks") or []
            if _has_pick_fields(item)
        ]
        verdict_raw = parsed.get("verdict")
        verdict: Verdict | None = None
        if isinstance(verdict_raw, dict) and _has_verdict_fields(verdict_raw):
            verdict = Verdict(
                text=str(verdict_raw["text"]),
                citation=int(verdict_raw["citation"]),
            )
        return SynthResult(kind="comparison", picks=picks, verdict=verdict)
    if kind == "factual":
        answer = str(parsed.get("answer") or "").strip()
        if not answer:
            return None
        citations = [
            int(c) for c in parsed.get("citations") or [] if isinstance(c, (int, float))
        ]
        return SynthResult(kind="factual", answer=answer, citations=citations)
    return None


def _has_pick_fields(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("reason"), str)
        and isinstance(item.get("citation"), (int, float))
    )


def _has_verdict_fields(item: dict[str, Any]) -> bool:
    return isinstance(item.get("text"), str) and isinstance(
        item.get("citation"), (int, float)
    )


def _pick_name_in_excerpt(name: str, excerpt_lower: str) -> bool:
    """Exact substring first, fallback to all-tokens-present (order-independent).

    The fallback catches legitimate rephrasings that substring misses:
    synth says "Fitbit Versa 4" and the source says "Versa 4 by Fitbit",
    or "Apple Watch Series 9" vs "the Watch Series 9 from Apple". Still
    strict enough to reject pure hallucinations, since every word in the
    name must appear somewhere in the excerpt.
    """
    lowered = name.lower()
    if lowered in excerpt_lower:
        return True
    tokens = re.findall(r"\w+", lowered)
    if not tokens:
        return False
    return all(tok in excerpt_lower for tok in tokens)


def _validate_picks(
    picks: list[Pick], sources: list[Source]
) -> tuple[list[Pick], list[Pick]]:
    """Keep a pick if its name is grounded in ANY source's excerpt.

    If the synth cited the wrong source number but the name appears
    elsewhere in the pool, repair the citation rather than drop; only pure
    hallucinations (name nowhere in any excerpt) get dropped. Qwen3 is
    often right about the pick and sloppy about which [N] to attach.
    """
    excerpts_lower = {s.number: s.excerpt.lower() for s in sources}
    numbers = [s.number for s in sources]
    kept: list[Pick] = []
    dropped: list[Pick] = []
    for pick in picks:
        cited = excerpts_lower.get(pick.citation)
        if cited is not None and _pick_name_in_excerpt(pick.name, cited):
            kept.append(pick)
            continue
        repaired: Pick | None = None
        for num in numbers:
            if num == pick.citation:
                continue
            if _pick_name_in_excerpt(pick.name, excerpts_lower[num]):
                repaired = replace(pick, citation=num)
                break
        if repaired is not None:
            log.debug(
                "research: pick repaired (name=%r [%d] -> [%d])",
                pick.name, pick.citation, repaired.citation,
            )
            kept.append(repaired)
        else:
            if log.isEnabledFor(logging.DEBUG):
                tokens = re.findall(r"\w+", pick.name.lower())
                diag = {
                    num: [t for t in tokens if t in excerpts_lower[num]]
                    for num in numbers
                }
                log.debug(
                    "research: pick dropped (name=%r cited=[%d]) "
                    "token hits per source: %s",
                    pick.name, pick.citation, diag,
                )
            dropped.append(pick)
    return kept, dropped


def _render_single_pick(pick: Pick) -> str:
    """Render when only one pick is grounded in the source pool.

    Phrased so the conversation LLM reads it as an incomplete answer and
    naturally asks a clarifying question (per the system prompt rules),
    rather than padding with fabricated picks from training data.
    """
    return (
        f"Only one pick is grounded in the available sources; "
        f"more context would help narrow it further.\n"
        f"- {pick.name}: {pick.reason} [{pick.citation}]"
    )


def _render_synth_result(result: SynthResult) -> str:
    if result.kind == "comparison":
        lines = [
            f"- {pick.name}: {pick.reason} [{pick.citation}]"
            for pick in result.picks
        ]
        body = "\n".join(lines)
        if result.verdict:
            body += (
                f"\nVerdict: {result.verdict.text} [{result.verdict.citation}]."
            )
        return body
    citations = " ".join(f"[{c}]" for c in result.citations)
    return f"{result.answer} {citations}".strip() if citations else result.answer


_PLANNER_PROMPT = """You decompose a research question into 1-{max_queries} web search queries.

The current year is {current_year}.

Rules:
- Output ONLY a JSON array. No prose, no markdown fences.
- Each item is an object with "query" (search string) and "intent" (what you hope to learn).
- For a single-hop factual lookup, emit ONE query. Do NOT inflate into sub-questions.
- For a multi-hop question (comparisons, causes, timelines), emit 2-4 queries
  targeting distinct sub-topics.
- For time-sensitive questions (best products, recommendations, recent news,
  current state of anything), append "{current_year}" to your queries so search
  results favor recent sources over outdated ones.
- Never exceed {max_queries} queries.

Examples

Question: What year did NASA land on the moon?
[{{"query": "Apollo 11 moon landing year", "intent": "confirm the year"}}]

Question: Why did Concorde stop flying?
[
  {{"query": "Concorde retirement reasons 2003", "intent": "primary cause of retirement"}},
  {{"query": "Concorde Air France crash 2000 aftermath", "intent": "safety concerns leading up"}}
]

Question: Compare Rust and Go for backend services
[
  {{"query": "Rust vs Go backend performance benchmarks {current_year}", "intent": "runtime tradeoffs"}},
  {{"query": "Rust vs Go ecosystem maturity {current_year}", "intent": "libraries and tooling"}},
  {{"query": "Rust vs Go hiring market {current_year}", "intent": "practical adoption"}}
]

Question: {question}
"""


_SYNTH_PROMPT = """You answer the user's question using ONLY the numbered sources below.

Sources:
{sources_block}

Output STRICT JSON ONLY. No prose, no markdown fences, no commentary.

Two response shapes:

For comparison / "best X" / "which X should I buy" questions, emit:
{{
  "kind": "comparison",
  "picks": [
    {{"name": "<brand + model>", "reason": "<1-2 sentence why, naming specifics>", "citation": <N>}}
  ],
  "verdict": {{"text": "<2-3 sentences, name the winner and the key tradeoff>", "citation": <N>}}
}}

Rules for comparison:
- Use 2-4 picks. Every "name" MUST appear verbatim in some source's excerpt.
- BEFORE picking, scan the excerpts for product names. If fewer than 2
  specific product names actually appear in the text, DO NOT invent picks
  from memory. Use the factual shape instead.
- Verdict should name the winner and explain WHY it won vs the runner-up
  (not just "X wins" - call out the specific tradeoff).

For factual / explanatory questions OR when sources lack the specifics the
question asks for, emit:
{{"kind": "factual", "answer": "<3-8 sentences>", "citations": [<N>, ...]}}

Use the factual shape when:
- The question assumes context the sources don't address (e.g. a specific
  device model, version, or budget that no source mentions).
- The sources cover the topic generally but not the specific angle asked.

When emitting factual for a "best X" question where sources are too general,
describe what the sources DO cover, note what's missing, and suggest what
the user could clarify (e.g. "Sources cover 2026 fitness trackers in
general but none mention iPhone 17 compatibility specifically; clarify
which iOS features matter to you.").

Rules:
- Use only citation markers in the range {marker_range}.
- Every answer MUST cite at least one source.

Question: {question}
"""


_REFINE_PROMPT = """You previously answered a research question using the
numbered sources below. The user has a follow-up drilling into the same
topic. Re-analyze the same sources through the lens of the follow-up.

Sources:
{sources_block}

Original question: {original_question}

Previous answer (for context):
{prior_answer}

Follow-up: {follow_up}

Output STRICT JSON ONLY using the SAME two shapes as the original synth:

For comparison / "best X" / "which X" follow-ups:
{{
  "kind": "comparison",
  "picks": [
    {{"name": "<brand + model>", "reason": "<1-2 sentences re: follow-up>", "citation": <N>}}
  ],
  "verdict": {{"text": "<2-3 sentences, name winner for THIS follow-up>", "citation": <N>}}
}}

For factual / explanatory follow-ups, or when the sources don't cover the
new angle:
{{"kind": "factual", "answer": "<3-8 sentences>", "citations": [<N>, ...]}}

Rules:
- Use only citation markers in the range {marker_range}.
- If the sources genuinely don't cover the follow-up angle, say so in the
  factual shape and describe what's missing - do NOT invent picks from
  training-data memory.
- Every "name" in comparison picks MUST appear verbatim in some source's
  excerpt. No exceptions.
- Every answer MUST cite at least one source.
"""
