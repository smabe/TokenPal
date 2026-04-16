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
a JSON array of 1-N queries (`[{"query": "...", "intent": "..."}]`).
`_parse_planner_output` handles prose-bracketed JSON, bare strings, and
a bare-question fallback.

Model is *not* asked to think on this stage. Fast is the goal.

### 2. Search

`_search_all` fans queries out to DuckDuckGo Lite via `asyncio.gather`
with a per-backend semaphore (rate-limit protection). Duplicate URLs
are collapsed. Cap is `max_fetches` (default 8).

Wikipedia is deliberately NOT in the fan-out. Its REST
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
  let it truncate the JSON), 700 otherwise.

The synth prompt explicitly instructs the model: if fewer than 2
product names actually appear in the source excerpts, DO NOT invent
picks from memory — emit `kind=factual` describing what the sources
cover and what the user could clarify. This is the anti-hallucination
fence.

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
