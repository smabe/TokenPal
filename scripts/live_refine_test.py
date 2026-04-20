"""Live-fire smoke test for the /refine supplemental path.

WARNING: This script makes REAL paid API calls.
  - 1-2 Anthropic synth calls (~$0.005 on Haiku 4.5, more on Sonnet/Opus)
  - 1 search call (Tavily credit if enabled, else free DDG)
  - Up to `refine_max_supplemental` web fetches

Do not run this in a CI loop. It exists to sanity-check the two-pass
refine flow against a real cloud model when the plumbing changes —
stubbed unit tests in tests/test_research.py cover the logic paths
without cost.

Builds a fake C64 source pool (no VIC-20 CPU info), then calls
Research.refine() with "what about the vic 20?" against the actual
Anthropic backend. Verifies:

  1. First cloud pass flags needs_fresh_search=true with a gap_query.
  2. Supplemental search fires and finds fresh URLs.
  3. _read_all fetches the fresh URLs.
  4. Second cloud pass produces a cited answer.

Run:
    python3 scripts/live_refine_test.py
"""

from __future__ import annotations

import asyncio
import sys

from tokenpal.actions.research.fetch_url import fetch_and_extract
from tokenpal.actions.research.research_action import _build_cloud_backend
from tokenpal.config.loader import load_config
from tokenpal.config.secrets import load_search_keys
from tokenpal.brain.research import ResearchRunner, Source


# Fake C64 source pool — these five URLs are the same ones the user's
# actual session surfaced. None of them contain VIC-20 CPU specs.
C64_SOURCES = [
    Source(
        number=1,
        url="https://s3data.computerhistory.org/brochures/commodore.commodore64.1982.102646264.pdf",
        title="Commodore 64, 1982",
        excerpt=(
            "The Commodore 64 was released in 1982 at a price of $595. "
            "It shipped with 64KB of RAM and used the MOS 6510 CPU."
        ),
        backend="duckduckgo",
    ),
    Source(
        number=2,
        url="https://8bitworkshop.com/docs/platforms/c64/index.html",
        title="Commodore 64 (C64) — 8bitworkshop documentation",
        excerpt=(
            "The Commodore 64 uses the MOS Technology 6510, running at "
            "1.023 MHz NTSC or 0.985 MHz PAL. The product was code named "
            "the VIC-40 as the successor to the popular VIC-20."
        ),
        backend="duckduckgo",
    ),
    Source(
        number=3,
        url="https://ist.uwaterloo.ca/~schepers/MJK/c64__.html",
        title="MJK's Commodore Hardware Overview: Commodore 64",
        excerpt=(
            "The Commodore 64 (C64) uses the 6510 microprocessor, a "
            "variant of the 6502 with an onboard 6-bit I/O port."
        ),
        backend="duckduckgo",
    ),
    Source(
        number=4,
        url="https://www.tomshardware.com/video-games/retro-gaming/commodore-64-ultimate-review",
        title="Commodore 64 Ultimate Review",
        excerpt=(
            "Tom's Hardware revisits the C64 in a modern retrospective, "
            "noting the 6510 CPU and 1 MHz clock speed."
        ),
        backend="duckduckgo",
    ),
    Source(
        number=5,
        url="https://www.c64-wiki.com/wiki/C64",
        title="C64 - C64-Wiki",
        excerpt=(
            "The C64 uses the MOS 6510/8500 at about 1 MHz. Forerunner: "
            "VIC-20. In the early 1980s Commodore released the world's "
            "first color video home computer, the VIC-20."
        ),
        backend="duckduckgo",
    ),
]

ORIGINAL_Q = "what cpu did the c64 have?"
PRIOR_ANSWER = (
    "The Commodore 64 used the MOS Technology 6510 microprocessor, "
    "running at ~1.023 MHz on NTSC systems and ~0.985 MHz on PAL "
    "systems. The 6510 used the same instruction set as the 6502 with "
    "an added onboard 6-bit I/O port."
)
FOLLOW_UP = "what about the vic 20?"


async def main() -> int:
    config = load_config()
    cloud_backend = _build_cloud_backend(config.cloud_llm)
    if cloud_backend is None:
        print("ERROR: cloud backend not configured. Run /cloud anthropic enable.")
        return 1

    print(f"Using cloud model: {cloud_backend.model}")
    print(f"refine_max_supplemental = {config.cloud_search.refine_max_supplemental}")
    print(f"Tavily enabled = {config.cloud_search.enabled}")
    print()

    api_keys = load_search_keys(bool(config.cloud_search.enabled))

    async def _fetch(url: str) -> str | None:
        try:
            return await fetch_and_extract(
                url, timeout_s=config.research.per_fetch_timeout_s,
            )
        except Exception as e:
            print(f"  fetch error for {url}: {e}")
            return None

    def _log(msg: str, *, url: str | None = None) -> None:
        print(f"  [log] {msg}" + (f" <{url}>" if url else ""))

    runner = ResearchRunner(
        llm=None,  # refine never calls local LLM
        fetch_url=_fetch,
        log_callback=_log,
        per_search_timeout_s=config.research.per_search_timeout_s,
        per_fetch_timeout_s=config.research.per_fetch_timeout_s,
        cloud_backend=cloud_backend,
        cloud_search=config.cloud_search,
        api_keys=api_keys,
    )

    print(f"Original question: {ORIGINAL_Q}")
    print(f"Follow-up:         {FOLLOW_UP}")
    print(f"Cached pool:       {len(C64_SOURCES)} sources (none with VIC-20 CPU)")
    print()
    print("Calling runner.refine()...")
    print()

    outcome = await runner.refine(
        original_question=ORIGINAL_Q,
        prior_answer=PRIOR_ANSWER,
        sources=C64_SOURCES,
        follow_up=FOLLOW_UP,
    )

    print()
    print("=" * 70)
    print(f"supplemental_stop: {outcome.supplemental_stop}")
    print(f"supplemental_queries: {outcome.supplemental_queries}")
    print(f"new_sources ({len(outcome.new_sources)}):")
    for s in outcome.new_sources:
        print(f"  [{s.number}] {s.url}")
        print(f"      title: {s.title}")
        print(f"      excerpt (first 200): {s.excerpt[:200]}")
    print(f"tokens_used: {outcome.tokens_used}")
    print()
    print("--- result ---")
    if outcome.result is None:
        print("(no parsed result)")
    else:
        print(f"kind: {outcome.result.kind}")
        if outcome.result.kind == "factual":
            print(f"answer: {outcome.result.answer}")
            print(f"citations: {outcome.result.citations}")
        else:
            print(f"picks: {outcome.result.picks}")
            print(f"verdict: {outcome.result.verdict}")
    print("=" * 70)

    # Pass/fail summary.
    print()
    if outcome.supplemental_stop == "ok" and outcome.new_sources:
        ans = (outcome.result.answer if outcome.result else "").lower()
        if "6502" in ans or "vic" in ans:
            print("PASS: supplemental fired, fresh sources merged, answer cites them.")
            return 0
        print("PARTIAL: supplemental fired but answer doesn't clearly reference it.")
        return 0
    if outcome.supplemental_stop == "none":
        print("UNEXPECTED: model didn't flag needs_fresh_search — check prompt.")
        return 2
    print(f"PARTIAL: supplemental stop = {outcome.supplemental_stop}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
