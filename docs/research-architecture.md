# Research Pipeline Architecture

TokenPal's `research` action answers user questions by pulling live
search results, fetching the actual article text, letting a local LLM
synthesize picks in a structured shape, and validating every claim
against the source text before rendering an answer the conversation LLM
echoes to the user.

This doc captures the current pipeline end-to-end: stages, invariants,
and the design tradeoffs future sessions need to know before editing.

## 5-stage pipeline

```
question
   │
   ▼
plan ──► search ──► fetch ──► synth ──► validate + render ──► answer
```

Each stage is independently testable. The runner
(`tokenpal/brain/research.py:ResearchRunner`) injects `fetch_url` as a
callable so tests can swap in mocks without touching the network.

### 1. Plan

`_plan(question)` calls the LLM once with `_PLANNER_PROMPT` and parses
a JSON array of 1-N queries (`[{"query": "...", "intent": "...", "backend": "..."}]`).
`_parse_planner_output` handles prose-bracketed JSON, bare strings, and
a bare-question fallback.

The `backend` field is optional. The prompt advertises routing hints
(stackexchange for code, hn for tech-news, tavily for product reviews,
brave/ddg for general web) so well-tuned LLMs pick the best source per
query. The dispatcher falls back safely on hallucinated / unconfigured
backends — see Stage 2.

Model is *not* asked to think on this stage. Fast is the goal.

### 2. Search

`_search_all` fans planner queries out via `asyncio.gather` with a
per-backend semaphore (`_BACKEND_CONCURRENCY`) for rate-limit protection.
Duplicate URLs are collapsed across queries. Cap is `max_fetches`
(default 8).

**Per-query backend routing.** Each `PlannedQuery` carries an optional
`backend` field emitted by the planner (see the Plan stage). The runner
normalizes it through `_resolve_backend`:

1. Empty → runtime default (`tavily` when cloud search is on, else `duckduckgo`)
2. `"ddg"` → alias for `duckduckgo`
3. `"tavily"` without a configured key → downgrades to `duckduckgo`
4. Unknown / typo → runtime default

**Backend table.**

| Backend       | Cost             | Key required | Preloaded content | When the planner picks it |
|---------------|------------------|--------------|-------------------|---------------------------|
| duckduckgo    | free             | no           | no                | default when cloud search off; "ddg" alias |
| tavily        | 2 credits/query (adv) | yes     | yes (full article body) | product comparisons, reviews, "best X" queries; default when cloud search on |
| brave         | free tier 2k/mo  | yes          | no                | general-web second opinion; keyed via `/cloud brave` or `TOKENPAL_BRAVE_KEY` |
| hn            | free             | no           | no                | tech-news / Show HN / startup launch discussion |
| stackexchange | free (300/day IP)| no           | no                | programming / code / API / error-message questions |
| wikipedia     | free             | no           | full article extract | NOT in fan-out (see below); `/ask` path only |

**Preloaded content short-circuit.** When a backend populates
`SearchResult.preloaded_content` (currently Tavily only), Stage 3 skips
`fetch_url` entirely and uses the field verbatim as the excerpt, running
the same sensitive-content filter inline. This is why Tavily runs are
~3× faster than DDG+fetch runs on the same question.

**Thin-pool top-up.** When cloud search is active and the Tavily batch
returns fewer than `_THIN_POOL_THRESHOLD=3` results, `_search_all`
refetches every query against DDG and merges the results (deduping by
URL). A visible warning lands in `session.warnings` and the transcript
so the user knows coverage was degraded. The top-up is Tavily-specific —
empty HN / SE / Brave batches do NOT trigger it.

**Wikipedia is deliberately NOT in the fan-out.** Its REST
`/page/summary/` endpoint requires exact article titles, while the
planner emits search-engine phrasings ("best X for Y 2026") which are
never article slugs. Every Wikipedia call 404'd. The `/ask` tool still
uses Wikipedia for factual one-shot lookups where the query IS a
title ("Apollo 11", "Mazda MX-5").

### 3. Read (fetch + extract)

For each search hit, `_read` either uses the search snippet or fetches
the article body. Fetching goes through
`tokenpal/actions/research/fetch_url.py:fetch_and_extract`, which has a
deliberate **two-stage** fetch strategy:

**Primary: newspaper4k with its own fetcher.** Many modern product-review
sites (cnet, tomsguide, whathifi, businessinsider) serve thin HTML to
aiohttp because they fingerprint based on TLS handshake + header combo,
not just UA. newspaper4k's built-in fetcher gets past those gates and
extracts real article text.

**Fallback: aiohttp + multi-extractor chain.** When newspaper misses, we
fetch the HTML ourselves and walk extractors in order: trafilatura
(precision → recall → default modes) → newspaper4k (given the HTML) →
readability. First result clearing the 300-char threshold wins;
otherwise the longest candidate is returned and upstream may discard it.

**Minimum extraction threshold: 300 chars.** Anything shorter is
almost always title-only dregs from a page the extractor couldn't
parse. The runner then falls back to the DuckDuckGo snippet, which at
least carries the query terms.

**Content sanitization.** Extracted text is scrubbed via
`contains_sensitive_content_term` (a narrow subset of SENSITIVE_APPS
containing only unambiguous identity-critical brand names like
`1password`, `venmo`, `whatsapp`). Common English words that happen to
be app names (`signal`, `messages`, `keychain`, `chase`, `fidelity`)
are deliberately excluded because substring-matching them against
article prose produces too many false positives and broke research on
legitimate consumer topics.

**Known limits**:
- Pure React SPAs (rtings.com) — no extractor can recover content
  without a JS-rendering browser step.
- Forbes — 403s our fetcher (bot-blocked on non-browser fingerprints).
- Any site with aggressive Cloudflare challenges.

### 4. Synth

`_synthesize` calls the LLM with `_SYNTH_PROMPT`, which demands strict
JSON matching `SYNTH_SCHEMA` (either `kind=comparison` with picks +
verdict, or `kind=factual` with answer + citations). The call uses:

- `enable_thinking=self._synth_thinking` (default True) — reasoning
  improves claim fidelity at ~5-10s latency cost per research run.
- `response_format={"type": "json_schema", "schema": SYNTH_SCHEMA}` —
  grammar-constrained on llama-server (near-impossible to violate),
  advisory on Ollama. `_parse_synth_json` tolerates prose chatter and
  malformed output by using `JSONDecoder().raw_decode` to find the first
  valid object anywhere in the text; prose-fallback path handles
  genuine parse failures.
- Max tokens 1800 with thinking on (thinking eats token budget, don't
  let it truncate the JSON), 700 otherwise. When `finish_reason=="length"`
  the synth logs `warning: synth hit max_tokens (N)` to the session log
  and `log.warning` so the downstream prose-fallback path is traceable
  instead of a silent parse failure.

The synth prompt explicitly instructs the model: if fewer than 2
product names actually appear in the source excerpts, DO NOT invent
picks from memory — emit `kind=factual` describing what the sources
cover and what the user could clarify. This is the anti-hallucination
fence.

### Cloud synth (opt-in Anthropic-backed path)

When `[cloud_llm] enabled = true` in `config.toml` AND an Anthropic API
key is stored at `~/.tokenpal/.secrets.json` (0o600), the synth stage
routes through `tokenpal/llm/cloud_backend.py:CloudBackend` instead of
the local LLM. Everything else in the pipeline (plan, search, fetch,
validate, render) is unchanged - the cloud path is a drop-in replacement
for stage 4 only.

**Enable via slash command, not by editing TOML:**
```
/cloud                     # status + fingerprint
/cloud enable <api-key>    # stores key, flips [cloud_llm] enabled = true
/cloud disable             # flip off, key retained
/cloud forget              # wipe key + disable
/cloud model <id>          # haiku-4-5 (default) | sonnet-4-6 | opus-4-7
```

**What crosses the wire:** only the `question` (already in the user's
chat log) and `sources_block` (public article text from the search
pool, already run through `contains_sensitive_content_term`). No
observations, app names, memory rows, voice profile, or conversation
history ever touch the cloud backend.

**Fallback is silent.** Any `CloudBackendError` (auth, rate limit,
network, timeout, bad request, or `no_credit` for an unfunded
workspace) logs a warning and falls back to local synth with identical
prompt + budget + schema. The user sees a one-line
`synth: cloud failed (<kind>), falling back to local` in the research
log; the answer still arrives.

**Schema enforcement.** `CloudBackend.synthesize` sends the
`SYNTH_SCHEMA` via `output_config.format` so Anthropic enforces valid
JSON server-side. This replaces the fragile `response_format` advisory
Ollama ignores - the cloud path almost never hits the prose-fallback
branch of `_parse_synth_json`.

**Cost.** Haiku 4.5 default runs ~$0.024 per research call at ~16K
input + ~1.5K output (3x cheaper than Sonnet, 5x cheaper than Opus).

See `plans/shipped/claude-api-research-synth.md` for the full
design rationale.

### Cloud web modes: search vs deep

Two opt-in cloud-driven modes replace the local pipeline with Sonnet-
driven server-side tools. Both require a Sonnet 4.6+ model.

**Search mode (`/cloud search on`).** Attaches only
`web_search_20260209`. Sonnet picks queries, reads filtered snippets
from the server, and synthesizes without ever fetching full pages.
Cheap (~$0.15-0.25/run typical) because search results are filtered
server-side — input tokens stay bounded. Good middle tier when you
want fresh-web awareness without the snowball cost.

**Deep mode (`/cloud deep on`).** Attaches `web_search_20260209` +
`web_fetch_20260209`. Sonnet fetches full pages server-side — handles
JS-heavy SPAs, bot-blocked sites, and paywalled previews the local
pipeline can't touch. **WARNING: $1-3/run** on review-heavy queries
because every `web_fetch` loads full page content into the tool-loop
context, and each subsequent step re-bills the accumulated input.
Slash handler surfaces the warning on activation.

If both flags are on, **deep wins** (logged as override). Config toggles
are mutually evaluated at runtime; no UI forbids setting both, but the
runner picks one.

### Deep mode (cloud-native web search)

`/cloud deep on` (or the modal checkbox) replaces stages 1-4 entirely
with a single agentic call through Anthropic's server-side
`web_search_20260209` + `web_fetch_20260209` tools. Sonnet or Opus
drives the search loop, reads pages server-side, and returns a
synthesized JSON answer with an inline `sources` array.

**When to use.** The local pipeline chokes on JS-heavy SPAs
(rtings.com), bot-blocked sites (Forbes), aggressive Cloudflare, and
paywalled previews - deep mode reaches all of those. For Wikipedia /
Reddit / most blogs, the local path is cheaper and often just as good.
Keep it off by default; flip it on when the topic demands it.

**Model gating.** Deep mode is **Sonnet 4.6+ only**. Haiku 4.5 falls
back to the older `web_search_20250305` tool which loads full results
into context (token cost explodes) and doesn't support adaptive
thinking. The `/cloud deep on` command refuses when the current
model is Haiku; the modal checkbox is disabled for the same reason.

**How it runs.** `CloudBackend.research_deep` sends one
`messages.create` call with both tools attached, `thinking: adaptive`,
and the deep-mode synth schema (`SYNTH_SCHEMA_DEEP` — adds a required
`sources` array). Anthropic's server orchestrates search → fetch →
synthesize. When the loop hits its built-in iteration cap the response
returns `stop_reason="pause_turn"`; we re-send the full message history
(original user + the assistant turn so far) and the API resumes -
**no "please continue" user follow-up**, that confuses the resume
path. We cap at 3 continuations total to bound worst-case cost.

**Source provenance.** Because Anthropic reads the pages server-side,
we never see the raw excerpts. `ResearchRunner.run_deep` builds
`Source` objects from the model-reported `sources` array with empty
excerpts and `backend="cloud"`. `_validate_picks` is **skipped** in
deep mode (no text to substring-match against); the trust model shifts
from "substring-grounded" to "Sonnet-with-adaptive-thinking plus
server-enforced JSON schema." Out-of-range citations still get
stripped; picks without a valid citation get dropped.

**Cache isolation.** The deep-mode and local-mode caches are keyed
separately: `sha256("deep:<q>")` vs `sha256("<q>")`. Same question run
both ways stores both answers; a `/research` re-run in the mode you're
currently in serves its own cached result, not the other mode's.

**/refine interaction.** `/refine` reuses cached source excerpts to
re-synthesize. Deep-mode sources have empty excerpts - `_handle_refine`
detects this (all excerpts empty) and refuses with a message pointing
the user to a fresh `/research` instead.

**Filter boundary.** The local path runs
`contains_sensitive_content_term` on article excerpts before they reach
synth. Deep mode bypasses this - Anthropic's content policies apply
server-side instead. Something to weigh before flipping it on for
sensitive-topic research.

**Cost.** ~2-3x a normal Sonnet /research call (search-tool billing
+ agentic-loop sampling overhead). Still cheap in absolute terms
($0.12-0.18/run vs $0.05); a 20-run week on deep mode is ~$2.50.

See `plans/shipped/cloud-native-web-search.md` for the full design.

### 5. Validate + render

Structured output from synth goes through `_finalize_answer`:

- **For `kind=comparison`**: every pick is checked via `_validate_picks`.
  A pick is valid iff its lowercased `name` appears in the cited
  source's lowercased excerpt, either as a substring or via
  all-tokens-present match (order-independent). This catches legit
  rephrasings ("Fitbit Versa 4" vs "Versa 4 by Fitbit") while still
  rejecting pure hallucinations.

  If the synth cited the wrong source number but the name appears in
  *any* other source in the pool, the citation is **repaired** rather
  than the pick dropped. Qwen3 is often right about the product and
  sloppy about which `[N]` to attach.

  Threshold logic:
  - `kept == 0`: render `"Sources don't name enough verifiable picks."`
  - `kept == 1`: render the single pick with `"more context would help
    narrow it further"` caveat — user gets a real answer, conversation
    LLM is steered to ask a clarifying question.
  - `kept >= 2`: render normally with picks + verdict.

- **For `kind=factual`**: citations are range-filtered against the
  source pool; the answer + in-range `[N]` markers are rendered.

Rendered output goes to `session.answer`, which the `research` action
wraps in `<tool_result>` delimiters for the conversation LLM.

## Telemetry

Every `run()` emits a one-line summary at exit (success, crash, or
early-return): `telemetry: mode=<backend>=<N>,... sources=<N> stopped=<reason>`.
The line lands in the session log (visible under `--verbose`) and is
the knob for measuring the actual backend mix post-ship. If
`mode=duckduckgo=3` dominates on runs where the planner picked Tavily,
that's the signal to investigate — either Tavily misconfiguration or
topical mismatch. If `mode=tavily=3` dominates with `sources < 3`
frequently, the thin-pool top-up is carrying more weight than we'd
like and Playwright becomes worth a look.

## Per-call thinking override

The synth opts into thinking per-call via a new kwarg on
`AbstractLLMBackend.generate()`: `enable_thinking: bool | None = None`.
`HttpBackend.generate()` dispatches per `inference_engine`:

- **llamacpp**: sends `chat_template_kwargs: {"enable_thinking": ...}`
  (always explicit, wins merge against the `--reasoning off` startup
  flag) plus `reasoning_format: "deepseek"` so thinking tokens land in
  a separate `reasoning_content` response field, leaving `content` as
  pure JSON.
- **ollama**: sends `reasoning_effort: "high"|"none"`.

The planner, observation, and conversation paths don't pass the kwarg
— they inherit the backend default (thinking off, fast quips). Only
synth flips it on.

## Conversation LLM contract

The conversation system prompt (`PersonalityEngine._tool_use_rule`)
instructs the LLM about the research tool's output shape:

- Before calling `research` on "best X for my Y" questions where Y is
  ambiguous (a specific device model, version, budget), ask ONE short
  clarifying question first.
- When summarizing the research result, list only picks that appear in
  the `<answer>`. DO NOT invent products or model numbers from memory,
  even ones the model is sure about.
- If the `<answer>` says sources don't have specifics or describes
  what's missing, echo that plainly and ask a clarifying question
  instead of fabricating picks to fill the gap.

This is the second anti-hallucination fence: even if synth escapes
with something ungrounded, the conversation LLM is told to not pad.

## Cache

Identical questions within `ResearchConfig.cache_ttl_s` (default 24h)
return the previous synthesis without re-fetching. Zero disables the
cache. To bust manually during development: vary the query wording or
set `cache_ttl_s = 0` in `config.toml`.

## Extension points

- **New search backends**: implement in
  `tokenpal/senses/web_search/client.py`, wire into `_BACKEND_CONCURRENCY`.
- **New extractors**: add to the chain in
  `tokenpal/actions/research/fetch_url.py:_extract`. Keep them graceful
  (return `""` on failure, no exceptions bubbled).
- **New synth shapes**: expand `SYNTH_SCHEMA` + `SynthResult` +
  `_build_synth_result` + `_render_synth_result`. Keep render tolerant
  of unknown kinds for forward compat.

## Failure modes (roughly ordered most to least common)

1. **Thin source pool** — fewer than `_THIN_POOL_THRESHOLD=3` sources
   come back. Logged as a warning; synthesis still runs but the
   `_finalize_answer` path is more likely to downgrade.
2. **All extractions empty** — every source falls back to DuckDuckGo
   snippets (~150 chars each). Synth often can't ground 2 picks; see
   single-pick render.
3. **Synth emits free-form prose despite JSON schema** — Ollama doesn't
   honor `response_format` grammar-constraint reliably. Prose-fallback
   path kicks in.
4. **Qwen3 sloppy citations** — names in source, wrong `[N]` attached.
   Citation-repair handles this.
5. **Pure hallucination** — name appears in no excerpt. Dropped. With
   all picks dropped, the user sees the downgrade message.

## Key files

- `tokenpal/brain/research.py` — runner, synth, parsers, validators,
  renderers, schemas, prompts.
- `tokenpal/actions/research/research_action.py` — `/research` command
  entrypoint, cache, consent gating.
- `tokenpal/actions/research/fetch_url.py` — two-stage fetch, extractor
  chain, sensitive-content filter.
- `tokenpal/llm/http_backend.py` — per-engine thinking dispatch,
  response_format forwarding.
- `tokenpal/brain/personality.py` — conversation system prompt rules
  governing pre-research clarification and anti-fabrication.
- `tokenpal/config/schema.py` — `ResearchConfig` (`synth_thinking`,
  `cache_ttl_s`, etc.), `InferenceEngine` literal type.
