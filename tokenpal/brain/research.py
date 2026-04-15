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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.senses.web_search.client import BackendName, SearchResult, search

log = logging.getLogger(__name__)

LogFn = Callable[[str], None]
FetchFn = Callable[[str], "asyncio.Future[str | None]"] | Callable[[str], Any]


class ResearchStopReason(StrEnum):
    COMPLETE = "complete"
    NO_QUERIES = "no_queries"
    NO_SOURCES = "no_sources"
    TOKEN_BUDGET = "token_budget"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    UNAVAILABLE = "unavailable"


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
        max_queries: int = 3,
        max_fetches: int = 5,
        token_budget: int = 6000,
        per_search_timeout_s: float = 5.0,
        per_fetch_timeout_s: float = 8.0,
    ) -> None:
        self._llm = llm
        self._fetch = fetch_url
        self._log = log_callback
        self._max_queries = max_queries
        self._max_fetches = max_fetches
        self._token_budget = token_budget
        self._per_search_timeout_s = per_search_timeout_s
        self._per_fetch_timeout_s = per_fetch_timeout_s
        self._semaphores: dict[BackendName, asyncio.Semaphore] = {
            name: asyncio.Semaphore(limit)
            for name, limit in _BACKEND_CONCURRENCY.items()
        }

    async def run(self, question: str) -> ResearchSession:
        session = ResearchSession(question=question)
        self._log(f"? {question}")

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

        hits = await self._search_all(session.queries)
        if not hits:
            session.stopped_reason = ResearchStopReason.NO_SOURCES
            return session

        capped = hits[: self._max_fetches]
        session.sources = await self._read_all(capped)
        for src in session.sources:
            self._log(f"  [{src.number}] {src.url}")

        if not session.sources:
            session.stopped_reason = ResearchStopReason.NO_SOURCES
            return session

        try:
            answer, used = await self._synthesize(question, session.sources)
        except Exception:
            log.exception("Research synthesizer failed")
            session.stopped_reason = ResearchStopReason.CRASHED
            return session

        session.tokens_used += used
        session.answer = _strip_dangling_markers(answer, len(session.sources))
        session.stopped_reason = ResearchStopReason.COMPLETE
        return session

    # ---- Stage 1: planner -------------------------------------------------

    async def _plan(self, question: str, session: ResearchSession) -> list[PlannedQuery]:
        prompt = _PLANNER_PROMPT.format(
            question=question, max_queries=self._max_queries
        )
        response = await self._llm.generate(prompt, max_tokens=400)
        session.tokens_used += response.tokens_used
        return _parse_planner_output(response.text, self._max_queries)

    # ---- Stage 2: search --------------------------------------------------

    async def _search_all(
        self, queries: list[PlannedQuery]
    ) -> list[SearchResult]:
        tasks = [self._one_search(q.query, "duckduckgo") for q in queries]
        tasks += [self._one_search(q.query, "wikipedia") for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        collected: list[SearchResult] = []
        seen: set[str] = set()
        for r in results:
            if isinstance(r, SearchResult) and r.source_url and r.source_url not in seen:
                collected.append(r)
                seen.add(r.source_url)
            elif isinstance(r, Exception):
                log.debug("search sub-task failed: %s", r)
        return collected

    async def _one_search(
        self, query: str, backend: BackendName
    ) -> SearchResult | None:
        sem = self._semaphores.get(backend)
        if sem is None:
            return None
        async with sem:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(search, query, backend=backend),
                    timeout=self._per_search_timeout_s,
                )
            except TimeoutError:
                log.debug("search timeout: %s (%s)", query, backend)
                return None
            except Exception:
                log.exception("search backend %s crashed", backend)
                return None

    # ---- Stage 3: read ----------------------------------------------------

    async def _read_all(self, hits: list[SearchResult]) -> list[Source]:
        """Fan fetches out in parallel with bounded concurrency so one slow
        host can't stall the pipeline. Source numbers match hit order."""
        sem = asyncio.Semaphore(3)

        async def _one(i: int, hit: SearchResult) -> Source | None:
            async with sem:
                return await self._read(i, hit)

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
                excerpt = str(fetched)[:2000]

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
    ) -> tuple[str, int]:
        sources_block = "\n\n".join(
            f"[{s.number}] {s.url}\n{s.excerpt}" for s in sources
        )
        marker_range = f"[1]..[{len(sources)}]"
        prompt = _SYNTH_PROMPT.format(
            sources_block=sources_block,
            question=question,
            marker_range=marker_range,
        )
        response = await self._llm.generate(prompt, max_tokens=600)
        return response.text.strip(), response.tokens_used


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


_PLANNER_PROMPT = """You decompose a research question into 1-{max_queries} web search queries.

Rules:
- Output ONLY a JSON array. No prose, no markdown fences.
- Each item is an object with "query" (search string) and "intent" (what you hope to learn).
- For a single-hop factual lookup, emit ONE query. Do NOT inflate into sub-questions.
- For a multi-hop question (comparisons, causes, timelines), emit 2-4 queries
  targeting distinct sub-topics.
- Never exceed {max_queries} queries.

Examples

Question: What year did NASA land on the moon?
[{{"query": "Apollo 11 moon landing year", "intent": "confirm the year"}}]

Question: Why did Concorde stop flying?
[
  {{"query": "Concorde retirement reasons 2003", "intent": "primary cause of retirement"}},
  {{"query": "Concorde Air France crash 2000 aftermath", "intent": "safety concerns leading up"}}
]

Question: Compare Rust and Go for backend services in 2025
[
  {{"query": "Rust vs Go backend performance benchmarks 2025", "intent": "runtime tradeoffs"}},
  {{"query": "Rust vs Go ecosystem maturity 2025", "intent": "libraries and tooling"}},
  {{"query": "Rust vs Go hiring market 2025", "intent": "practical adoption"}}
]

Question: {question}
"""


_SYNTH_PROMPT = """You answer the user's question using ONLY the numbered sources below.

Sources:
{sources_block}

Citation rules:
- Cite every factual claim with a bracketed marker like [1] or [3].
- Only use markers in the range {marker_range}. Unknown markers will be stripped.
- If the sources disagree or don't cover the claim, say so plainly — do NOT fabricate.
- Keep the answer under 6 sentences.

Question: {question}

Remember: cite every claim with a bracketed marker in the range {marker_range}.
Unsupported claims get stripped.
"""
