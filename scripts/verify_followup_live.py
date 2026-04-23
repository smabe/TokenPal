"""Live verification of the smarter-buddy follow-up feature.

Hits the real Anthropic API. Uses the key from ~/.tokenpal/.secrets.json.
Defaults to Haiku 4.5 (cheapest); pass `--sonnet` to run against Sonnet 4.6
(needed for the search/deep modes but NOT for synth-mode follow-ups).

Usage:
    .venv/bin/python scripts/verify_followup_live.py [--sonnet]

What it does:
1. Simulates a synth-mode /research on the Immich Live Photo question.
2. Stashes a FollowupSession with the reconstructed [user, assistant] messages.
3. Runs TWO follow-ups back-to-back:
   - Followup #1: creates the prompt cache (cache_creation > 0, cache_read = 0).
   - Followup #2: within 5min ephemeral TTL — should HIT the cache
     (cache_read_tokens > 0).
4. Prints telemetry + estimated cost per call.

Exit code 0 = done criteria satisfied (non-zero cache reads observed).
"""

from __future__ import annotations

import argparse
import sys
import time

from tokenpal.brain.research import Source
from tokenpal.brain.research_followup import FollowupSession, bump
from tokenpal.config.secrets import get_cloud_key
from tokenpal.llm.cloud_backend import CloudBackend


# Realistic Immich sources with bulkier excerpts so the prompt crosses
# Anthropic's minimum-cacheable-prefix thresholds (Haiku ~2048 tokens,
# Sonnet/Opus ~1024 tokens). Content is plausible GitHub-issue chatter
# shaped after the real issues that the /cloud search run cited.
_IMMICH_SOURCES = [
    Source(
        number=1,
        url="https://github.com/immich-app/immich/issues/20023",
        title="Live Photo thumbnails not generated after upload from iOS",
        excerpt=(
            "Steps to reproduce: upload a Live Photo (HEIC + MOV pair) "
            "from the iOS companion app on an iPhone 14 Pro running "
            "iOS 18.3, running Immich server v1.120.2 on Docker. "
            "Expected: thumbnail renders in web UI timeline and album "
            "views. Actual: tile is blank / grey placeholder, but "
            "clicking the asset plays the live-photo video back on "
            "hover, and the download button returns the full HEIC "
            "at original resolution. Several users confirm: files "
            "are stored intact in /upload but the thumbs/ directory "
            "has no corresponding preview. The Microservices worker "
            "log shows the thumbnail job enqueuing but then silently "
            "dropping the asset with no error message at log level "
            "INFO; at DEBUG you see a libvips warning about the MOV "
            "sidecar being treated as the primary file. Workaround "
            "that worked for ~70% of reporters: Administration → "
            "Jobs → 'Generate Thumbnails' → click Missing. Some "
            "users needed to run it twice, and two reported having "
            "to delete the existing empty thumbs subdirectories and "
            "re-run. A handful had to upgrade off v1.119 where a "
            "sidecar-pairing regression was later fixed in v1.121."
        ),
        backend="github_issues",
    ),
    Source(
        number=2,
        url="https://github.com/immich-app/immich/discussions/20548",
        title="Live Photo thumb regen not sticking — docker restart fix",
        excerpt=(
            "OP tried 'Generate Thumbnails → Missing' three times, "
            "each time queuing ~400 assets and completing without "
            "errors, but the blank tiles remained. What finally "
            "worked: `docker compose down && docker compose up -d` "
            "for a full stack restart (NOT just the microservices "
            "container), followed by queuing 'Generate Thumbnails "
            "→ All' (not Missing). Theory from a maintainer in "
            "thread: the in-memory queue state gets stuck in a "
            "pseudo-completed state after partial failures, and "
            "only a fresh process lifecycle clears it. 'All' is "
            "slower because it reprocesses every asset, but for "
            "Live Photos specifically the 'Missing' predicate "
            "seems to falsely match pairs whose video sidecar has "
            "been indexed but whose HEIC thumb generation silently "
            "failed. Same thread also mentions clearing the "
            "browser cache on the client side since Immich "
            "aggressively caches placeholder images; an incognito "
            "reload confirmed the new thumbs were actually present."
        ),
        backend="github_issues",
    ),
    Source(
        number=3,
        url="https://github.com/immich-app/immich/issues/13326",
        title="HEIC/MOV pairing regression in 1.119-1.120 — fixed in 1.121",
        excerpt=(
            "Root-cause analysis: the Live Photo sidecar pairing "
            "logic was refactored in v1.119 to deduplicate assets "
            "based on iOS-provided metadata tags, but the new "
            "dedupe pass had an off-by-one bug where the MOV "
            "sidecar was promoted as the primary asset if its "
            "timestamp hash collided with the HEIC's. Effect: "
            "thumbnail generation would run against the MOV file, "
            "succeed, produce a video-frame preview, but the "
            "database row pointing at the HEIC-backed asset would "
            "end up with no thumb_path set. Fix landed in 1.121: "
            "set IMMICH_VERSION=release in .env and run "
            "`docker compose pull && docker compose up -d`. "
            "Upgrading alone is not enough; users on this fix "
            "also need to run 'Generate Thumbnails → All' once "
            "to repopulate the affected rows. Users running "
            "docker-compose-unraid-template-style setups should "
            "also verify the .env was actually re-read after "
            "pull — the template ships with an override file "
            "that some users edited instead."
        ),
        backend="github_issues",
    ),
    Source(
        number=4,
        url="https://tidyrepo.com/how-to-fix-immich-error-loading-image-a-quick-guide/",
        title="How to fix Immich error loading image — browser-side",
        excerpt=(
            "After server-side thumbnail regeneration completes, "
            "a subset of users still see 'Error loading image' "
            "placeholders in the web UI. The cause is usually "
            "the PWA's IndexedDB + service-worker cache holding "
            "onto the 404 responses from the period when thumbs "
            "were missing. Fix: DevTools → Application tab → "
            "Storage → Clear Site Data (everything), reload. "
            "For the mobile Immich app, settings → Advanced → "
            "'Prefer remote images' will bypass the local "
            "thumbnail cache for display. Also worth checking: "
            "if you use a reverse proxy (nginx, Traefik, Caddy) "
            "in front of Immich, confirm it isn't rewriting or "
            "intercepting responses for /api/asset/*/thumbnail. "
            "Several users had Caddy configurations that were "
            "overly aggressive on caching 404s at the proxy layer."
        ),
        backend="tidyrepo",
    ),
    Source(
        number=5,
        url="https://github.com/immich-app/immich/discussions/9080",
        title="Thumb regen: Missing vs All — which to run?",
        excerpt=(
            "Maintainer FAQ answer: 'Missing' only reprocesses "
            "assets whose thumb_path is NULL in the database. "
            "If your problem is that thumbs exist on disk but "
            "are blank/corrupted/wrong-format, 'Missing' skips "
            "them. Use 'All' in that case. If your problem is "
            "that the DB has a thumb_path but the file doesn't "
            "exist on disk (common after bind-mount shuffles "
            "or moving the upload volume), you need 'Missing' "
            "AFTER first running 'rm -rf /your/upload/path/"
            "thumbs/*' to force re-creation. For Live Photos "
            "specifically, always pair the thumb regen with "
            "a 'Detect Faces' rerun because the pairing bug "
            "sometimes left face_detection rows pointing at "
            "the MOV sidecar instead of the HEIC."
        ),
        backend="github_discussions",
    ),
]


_PROMPT_TEMPLATE = """You answer the user's question using ONLY the numbered sources below.

Sources:
{sources_block}

Question: {question}

Respond with a concise 2-3 paragraph answer that cites sources using [N] markers.
"""


def _build_prompt(question: str) -> str:
    sources_block = "\n\n".join(
        f"[{s.number}] {s.url}\n{s.excerpt}" for s in _IMMICH_SOURCES
    )
    return _PROMPT_TEMPLATE.format(
        sources_block=sources_block, question=question,
    )


# Haiku 4.5 pricing (per 1M tokens, USD) as of 2026-04.
# Docs: https://docs.anthropic.com/en/docs/about-claude/pricing
_PRICING = {
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,   # 1.25x input
        "cache_read": 0.10,    # 0.10x input
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
    },
}


def _estimate_cost(model: str, *, input_tokens: int, output_tokens: int,
                   cache_creation: int, cache_read: int) -> float:
    """Rough cost estimate based on published pricing."""
    p = _PRICING.get(model)
    if p is None:
        return 0.0
    # input_tokens is the total billable input MINUS cached hits.
    # cache_creation is billed at write rate; cache_read at read rate.
    # Uncached input = input_tokens - cache_creation - cache_read.
    uncached = max(0, input_tokens - cache_creation - cache_read)
    return (
        uncached * p["input"]
        + cache_creation * p["cache_write"]
        + cache_read * p["cache_read"]
        + output_tokens * p["output"]
    ) / 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sonnet", action="store_true",
                        help="Use claude-sonnet-4-6 instead of haiku-4-5.")
    args = parser.parse_args()

    model = "claude-sonnet-4-6" if args.sonnet else "claude-haiku-4-5"

    key = get_cloud_key()
    if not key:
        print("FAIL: no cloud key at ~/.tokenpal/.secrets.json", file=sys.stderr)
        return 2

    backend = CloudBackend(api_key=key, model=model)

    question = (
        "I have an issue with the program immich. it won't render "
        "thumbnails or previews for live photos I upload with my iphone. "
        "it will however let me download full resolution stored images "
        "and when I mouse over a live photo the video plays. I can't "
        "find any concrete fix online."
    )
    prompt = _build_prompt(question)

    # --- Simulate the initial /research (synth mode) ---
    print(f"\n=== initial /research synth ({model}) ===")
    print(f"prompt chars: {len(prompt)}")
    t0 = time.monotonic()
    initial = backend.synthesize(prompt, max_tokens=1200)
    dt = time.monotonic() - t0
    print(f"answer chars: {len(initial.text)} | output_tokens={initial.tokens_used}"
          f" | latency={dt:.1f}s")
    print("answer (first 400 chars):")
    print(initial.text[:400] + ("..." if len(initial.text) > 400 else ""))

    # --- Build a FollowupSession as the orchestrator would ---
    session = FollowupSession(
        mode="synth",
        model=model,
        sources=list(_IMMICH_SOURCES),
        messages=[
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": initial.text},
        ],
        tools=[],
        ttl_s=900,
        max_followups=5,
    )

    # --- Followup #1 (creates the cache) ---
    print(f"\n=== followup #1 ({model}) — priming the cache ===")
    t0 = time.monotonic()
    f1 = backend.followup(
        session.messages, session.tools,
        "I already tried the thumbnails regen job. What else can I try?",
    )
    dt = time.monotonic() - t0
    cost1 = _estimate_cost(
        model,
        input_tokens=f1.cache_creation_tokens + f1.cache_read_tokens,
        output_tokens=f1.tokens_used,
        cache_creation=f1.cache_creation_tokens,
        cache_read=f1.cache_read_tokens,
    )
    # input_tokens from usage isn't directly exposed; recompute from cache parts.
    # The non-cached input is small (just the new user turn).
    print(f"cache_creation={f1.cache_creation_tokens} "
          f"cache_read={f1.cache_read_tokens} "
          f"output={f1.tokens_used} latency={dt:.1f}s "
          f"est_cost=${cost1:.4f}")
    print("answer (first 400 chars):")
    print(f1.text[:400] + ("..." if len(f1.text) > 400 else ""))
    session.messages = f1.messages
    session.total_cache_read_tokens += f1.cache_read_tokens
    session.total_cache_creation_tokens += f1.cache_creation_tokens
    bump(session)

    # --- Followup #2 (prefix grows; crosses cache threshold if it didn't already) ---
    print(f"\n=== followup #2 ({model}) ===")
    t0 = time.monotonic()
    f2 = backend.followup(
        session.messages, session.tools,
        "Is there a version-specific fix I should check first?",
    )
    dt = time.monotonic() - t0
    cost2 = _estimate_cost(
        model,
        input_tokens=f2.cache_creation_tokens + f2.cache_read_tokens,
        output_tokens=f2.tokens_used,
        cache_creation=f2.cache_creation_tokens,
        cache_read=f2.cache_read_tokens,
    )
    print(f"cache_creation={f2.cache_creation_tokens} "
          f"cache_read={f2.cache_read_tokens} "
          f"output={f2.tokens_used} latency={dt:.1f}s "
          f"est_cost=${cost2:.4f}")
    print("answer (first 400 chars):")
    print(f2.text[:400] + ("..." if len(f2.text) > 400 else ""))
    session.messages = f2.messages
    session.total_cache_read_tokens += f2.cache_read_tokens
    session.total_cache_creation_tokens += f2.cache_creation_tokens
    bump(session)

    # --- Followup #3 (should HIT the cache created by #2) ---
    print(f"\n=== followup #3 ({model}) — expect cache hit from #2 ===")
    t0 = time.monotonic()
    f3 = backend.followup(
        session.messages, session.tools,
        "If I'm running v1.121 already, what else could cause this?",
    )
    dt = time.monotonic() - t0
    cost3 = _estimate_cost(
        model,
        input_tokens=f3.cache_creation_tokens + f3.cache_read_tokens,
        output_tokens=f3.tokens_used,
        cache_creation=f3.cache_creation_tokens,
        cache_read=f3.cache_read_tokens,
    )
    print(f"cache_creation={f3.cache_creation_tokens} "
          f"cache_read={f3.cache_read_tokens} "
          f"output={f3.tokens_used} latency={dt:.1f}s "
          f"est_cost=${cost3:.4f}")
    print("answer (first 400 chars):")
    print(f3.text[:400] + ("..." if len(f3.text) > 400 else ""))
    bump(session)

    # --- Verdict ---
    print("\n=== verdict ===")
    prior_prefix_tokens_estimate = len(prompt) // 4 + len(initial.text) // 4
    print(f"f1 prefix tokens (est): {prior_prefix_tokens_estimate}")
    print(f"f1: create={f1.cache_creation_tokens} read={f1.cache_read_tokens}")
    print(f"f2: create={f2.cache_creation_tokens} read={f2.cache_read_tokens}")
    print(f"f3: create={f3.cache_creation_tokens} read={f3.cache_read_tokens}")
    print(f"f3 cost: ${cost3:.4f} (target ≤ $0.07)")
    print(f"session followup_count: {session.followup_count}/{session.max_followups}")

    ok = True
    any_cache_write = max(
        f1.cache_creation_tokens, f2.cache_creation_tokens,
        f3.cache_creation_tokens,
    )
    if any_cache_write == 0:
        print("FAIL: no follow-up created a cache entry. Check that the "
              "prefix crosses Anthropic's minimum (~1024 tokens Sonnet, "
              "~2048 Haiku).")
        ok = False
    if f3.cache_read_tokens == 0:
        print("FAIL: followup #3 did not hit the cache written by #2. "
              "Possible: cache_control breakpoint drift, >5min between "
              "calls, SDK version skew.")
        ok = False
    if cost3 > 0.07:
        print(f"WARN: followup #3 cost ${cost3:.4f} exceeds $0.07 target.")
        ok = False
    if ok:
        print("PASS: prompt caching is active on follow-ups.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
