# Claude API for `/research` synth (opt-in cloud backend)

## Goal
Route `/research`'s synth stage (stage 4 of the pipeline) through the Claude API using `claude-haiku-4-5`, so the heaviest single LLM call in TokenPal gets a faster and more capable brain without changing anything else. Every other call site — observations, freeform, conversation, planner, `/ask`, idle-tool rolls — keeps running locally. Opt-in via `ANTHROPIC_API_KEY` env var (setting the key is the consent).

## Non-goals
- **No cloud for the observation loop.** App names, window titles, typing cadence, etc. never leave the machine. This is the load-bearing privacy story — do not soften it.
- **No cloud for conversation, planner, or `/ask`.** Conversation uses persistent history and runs constantly; planner is a fast pre-stage; `/ask` is one-shot and local search already works. Only synth gets cloud.
- **No third provider** (OpenAI, Groq, etc.). Anthropic only. If we ever want to generalize, that's a separate plan.
- **No consent UI beyond the slash command itself.** Running `/cloud enable <key>` IS the consent — typing it is intentional, not an accident. No first-use warning prompt, no `/consent` category. If no key is stored, we silently use local synth.
- **No batch API, no streaming, no managed agents, no tool use.** Synth is one request, one response, bounded output (~1500 tokens). Plain `messages.create()`.
- **No new model registry, no auto-adopt, no `/model cloud` command.** Model string is a config value; if the user wants to bump it to Sonnet, they edit `config.toml`.
- **Don't touch the local synth code path.** Keep `_synthesize()` intact as the fallback; the cloud path is a siblinged branch.

## Why Haiku 4.5 (not Sonnet)
Pricing (per 1M tokens, from Anthropic's catalog as of 2026-04):

| Model             | Input      | Output     | Context |
|-------------------|------------|------------|---------|
| Haiku 4.5         | $1.00      | $5.00      | 200K    |
| Sonnet 4.6        | $3.00      | $15.00     | 1M      |
| Opus 4.7          | $5.00      | $25.00     | 1M      |

**Haiku is exactly 3× cheaper than Sonnet on both input and output, and 5× cheaper than Opus.** A typical `/research` synth is ~16K input (sources block) + ~1.5K output:
- **Haiku**: 16 × $1/1M + 1.5 × $5/1M ≈ **$0.024/run**
- **Sonnet**: ≈ $0.071/run
- **Opus**: ≈ $0.118/run

At ~10 research runs/day that's ~$0.24/day on Haiku vs ~$0.71 on Sonnet. Haiku 4.5 is also the fastest tier (targeted at speed-critical work), so the user-visible latency win over Qwen3-14B local synth is big. If Haiku proves under-spec on multi-source synthesis, the user runs `/cloud model claude-sonnet-4-6`.

## Slash command UX
One command, three verbs. Mirrors how `/zip`, `/senses`, and `/voice` already work.

```
/cloud                      # show status: enabled/disabled, model, key fingerprint, last-used timestamp
/cloud enable <api-key>     # store key at ~/.tokenpal/.secrets.json (0o600), flip enabled=true, confirm
/cloud disable              # flip enabled=false (keeps the key on disk so re-enable is one word)
/cloud forget               # wipe the key from disk entirely
/cloud model <model-id>     # change model (validated against a small allowlist: haiku-4-5, sonnet-4-6, opus-4-7)
```

Behavior:
- `/cloud enable <key>` validates the key shape (starts with `sk-ant-`, reasonable length) — rejects obvious typos without hitting the network. Does NOT probe the API at enable time; first real call validates. If the probe fails later, `/cloud` status shows "key rejected (401) — run /cloud enable with a new key".
- The key is stored at `~/.tokenpal/.secrets.json` with 0o600 perms, mirroring the existing `.consent.json` pattern in `tokenpal/config/consent.py`. Never written to `config.toml`, never logged, never echoed back in chat bubbles or status lines.
- `/cloud` (bare) shows a redacted fingerprint like `sk-ant-...a3f2` — last 4 chars only — so the user can confirm which key is active without leaking it.
- `/cloud enable` re-echoes the raw key the user just typed in the chat log as a privacy risk. The command handler MUST scrub the raw key from the chat log line before it renders (same pattern the existing chat log uses for sensitive app filtering). Only the fingerprint appears.
- The `[cloud_llm]` config section in `config.toml` still exists (for `model`, `timeout_s`) so power users who DO want to edit TOML can; the slash command is the primary path but not the only one. The key is never in TOML.
- Enabling via the slash command takes effect immediately — no restart — because the `ResearchRunner` is built fresh per `/research` invocation. Disabling is also immediate.

## Files to touch
- `tokenpal/llm/cloud_backend.py` — NEW. Thin `AnthropicCloudBackend` wrapper with a single method `synthesize(prompt, schema, max_tokens) -> CloudResponse`. Not an `AbstractLLMBackend` subclass — we don't want it accidentally used for observations. Uses the official `anthropic` SDK (`pip install anthropic`). Constructor takes `api_key`, `model`, `timeout_s`. Raises `CloudBackendError` on any failure (no partial/silent falls-through here — caller decides).
- `tokenpal/brain/research.py` — inject an optional `cloud_backend: AnthropicCloudBackend | None` into `ResearchRunner.__init__`. In `_synthesize()`, if cloud backend is set, try cloud first; on any `CloudBackendError` log warning and fall back to `self._llm.generate(...)`. Keep the rest of the stage (`_parse_synth_json`, validation, rendering) exactly the same — the cloud path produces the same `SynthResult` shape.
- `tokenpal/config/schema.py` — NEW `CloudLLMConfig` dataclass (`enabled: bool = False`, `provider: str = "anthropic"`, `model: str = "claude-haiku-4-5"`, `timeout_s: float = 30.0`, `research_synth: bool = True`). Attach to top-level `Config` as `cloud_llm`. Default all-off: `enabled=False`. **No `api_key_env` field** — the key lives in `.secrets.json`, not env-var-indirected.
- `tokenpal/config/config.default.toml` — NEW `[cloud_llm]` section with commented-out model override + the one-line privacy note ("only used for /research synth, never for observations"). No key field.
- `tokenpal/config/secrets.py` — NEW. Thin reader/writer for `~/.tokenpal/.secrets.json` with 0o600 perms. API: `get_cloud_key() -> str | None`, `set_cloud_key(key: str) -> None`, `clear_cloud_key() -> None`, `fingerprint(key: str) -> str` (returns `sk-ant-...XXXX`). Mirrors the shape of `tokenpal/config/consent.py`. File gitignored.
- `tokenpal/config/cloud_writer.py` — NEW. Upserts `[cloud_llm] enabled = true/false` and `model = "..."` in `config.toml` (the non-secret half of the cloud state). Mirrors `tokenpal/config/senses_writer.py`.
- `tokenpal/commands/cloud.py` — NEW. `/cloud`, `/cloud enable <key>`, `/cloud disable`, `/cloud forget`, `/cloud model <id>`. Imports `secrets.py` + `cloud_writer.py`. Returns a status bubble for each verb. Scrubs the raw key from the chat log line on `/cloud enable` (same mechanism used by sensitive-app filtering elsewhere).
- `tokenpal/app.py` (or wherever slash dispatch lives) — wire `/cloud` into the command registry.
- `tokenpal/actions/research/research_action.py` — wire the runner construction: when `cfg.cloud_llm.enabled` is True AND `secrets.get_cloud_key()` returns non-empty AND `cfg.cloud_llm.research_synth` is True, build the `AnthropicCloudBackend` and pass it to `ResearchRunner`. Otherwise pass `None`. Log one line per `/research` call: `"synth: cloud (haiku-4.5)"` or `"synth: local (<model>)"` so the user knows which path ran.
- `pyproject.toml` (or wherever deps live) — add `anthropic>=0.45.0` as a dependency. Gate the import in `cloud_backend.py` behind a try/except at module top so a user who doesn't install it gets a clean "install anthropic" error, not an ImportError crash.
- `docs/research-architecture.md` — add a "Cloud synth" subsection under Stage 4 explaining the fallback behavior, privacy boundary (sources block + question go to Anthropic; nothing else does), and how to enable.
- `CLAUDE.md` — one line under `## LLM Notes`: cloud synth is opt-in via `ANTHROPIC_API_KEY`, only affects `/research`, falls back silently.
- `tests/test_cloud_backend.py` — NEW. Mock the Anthropic client, verify: (a) happy path returns a parseable `CloudResponse`, (b) timeout raises `CloudBackendError`, (c) network error raises `CloudBackendError`, (d) rate limit raises `CloudBackendError`. Don't hit the real API in tests.
- `tests/test_research_runner.py` — extend. Add a case where the runner has a cloud backend; on cloud success the local `_llm.generate` is NOT called; on cloud failure local IS called and produces the same final answer.
- `tests/test_cloud_command.py` — NEW. Cover every `/cloud` verb: enable stores+fingerprints and returns status, disable flips config without wiping key, forget removes both, model validates allowlist, enable with bad key shape rejects without hitting the network, enable scrubs the raw key from the returned status string, `.secrets.json` is written at 0o600.

## Anthropic API specifics (so the plan doesn't rot)
- **Model ID**: `claude-haiku-4-5` (exact string; no date suffix). Sonnet upgrade path: `claude-sonnet-4-6`.
- **SDK**: official `anthropic` package, `anthropic.Anthropic(api_key=...)`, `.messages.create()`.
- **No thinking**: Haiku 4.5 errors on `output_config.effort` and is speed-tier. Don't send `thinking` or `effort` — use defaults.
- **Structured output**: use `output_config={"format": {"type": "json_schema", "schema": SYNTH_SCHEMA}}` so we get the same JSON contract as local llama-server's grammar-constrained path. This replaces the flakiest part of the local pipeline (Ollama ignoring `response_format`). Use `messages.create()` with explicit `output_config`, not `.parse()` — we already have `_parse_synth_json` for the local fallback path and want symmetric handling.
- **No prompt caching**: the stable prefix of our synth prompt is ~500-800 tokens, under Haiku's 4096-token minimum cacheable prefix. Would write but never read. Skip.
- **`max_tokens`**: set to 1800 (same as local thinking budget) to leave headroom for JSON. Haiku's cap is way above this; non-streaming is fine.
- **Errors to catch**: `anthropic.APIConnectionError`, `anthropic.RateLimitError`, `anthropic.APIStatusError`, `anthropic.AuthenticationError`. All wrap into `CloudBackendError`. SDK auto-retries 429/5xx with `max_retries=2` by default — leave that on.
- **Headers / betas**: none needed for plain `messages.create()` on Haiku 4.5.

## Privacy accounting
What crosses the wire when cloud synth is enabled:
- The `question` (user's `/research` query — already logged locally, already shown in the chat log).
- The `sources_block` — URLs + extracted article text from the public web. Already public content. Not user-generated.
- No observations, no app names, no screen text, no memory rows, no voice profile, no conversation history.

What doesn't change:
- Local logs stay local. `memory.db` stays local. 0o600 perms stay.
- If the user disables cloud synth, zero network calls to Anthropic.
- The `contains_sensitive_content_term` filter on extracted article text still runs BEFORE the cloud call (it's in `fetch_url.py`, upstream of `_synthesize`). So sensitive-brand pages are already scrubbed by the time they'd hit the wire.

## Failure modes to anticipate
- **Key present but invalid**: 401 on first call. Wrap, log "cloud auth failed — falling back to local synth", caller proceeds with local. Don't disable cloud permanently — user may have fixed the key between runs.
- **Workspace unfunded**: Anthropic requires min $5 credit before a key works. An unfunded key returns 403 with a body like `"Your credit balance is too low to access the Claude API"`. Distinct from generic 401 — `/cloud` status should show "no credit (add funds at console.anthropic.com)" instead of "key rejected". Detect by substring-matching the 403 error message for `"credit balance"`.
- **Rate limit**: SDK retries twice with backoff, then raises. Fall back to local. Log the `retry-after` hint.
- **Network down**: `APIConnectionError` → fall back to local. No user-visible error — `/research` still works.
- **Schema mismatch**: Anthropic's `output_config.format` should guarantee valid JSON matching `SYNTH_SCHEMA`. If somehow we get back malformed text, `_parse_synth_json` handles it the same way it handles local output.
- **Cloud returns a `refusal` stop_reason**: rare but possible for adversarial queries. Treat as "no synth result" — fall back to local.
- **Key stored but `cloud_llm.enabled = false`**: config wins. No cloud calls. `/cloud` status shows "disabled (key stored — run /cloud enable to resume)".
- **Raw key echoes in chat log**: the user's literal `/cloud enable sk-ant-...` input needs scrubbing before it hits the chat log or any debug log. Verify in the test suite — a test grep over the captured log output must not contain the raw key, only the fingerprint.
- **SDK not installed**: graceful message at runner construction; cloud silently disabled.
- **Timeout**: 30s default. If local synth regularly takes 20s+, cloud should be much faster (Haiku), but set the timeout high enough that a slow TCP handshake doesn't flap. On timeout → fall back.

## Done criteria
- `tokenpal --validate` shows a new "Cloud LLM" line: "disabled" (default), "enabled: haiku-4.5 (key: sk-ant-...a3f2)", or "enabled but no key — run /cloud enable".
- `/cloud enable sk-ant-test123...` stores the key at `~/.tokenpal/.secrets.json` (0o600), returns a bubble like "Cloud LLM enabled — haiku-4.5 (sk-ant-...3123)". Next `/research` routes synth through Anthropic.
- `/cloud disable` returns "Cloud LLM disabled — key retained". Next `/research` uses local synth.
- `/cloud forget` returns "Cloud LLM disabled and key wiped". `.secrets.json` has no `cloud_key` field afterwards.
- Running `/cloud enable sk-ant-...` and then grepping the chat log file for that raw key finds zero matches — only the fingerprint.
- Unsetting via `/cloud disable` or `/cloud forget` makes `/research` behave byte-for-byte identically to today. No diff in answer shape, rendering, cache, or validate-then-render flow.
- `pytest tests/test_cloud_backend.py tests/test_research_runner.py` green.
- `ruff check tokenpal/llm/cloud_backend.py tokenpal/brain/research.py tokenpal/config/schema.py` clean.
- `mypy tokenpal/llm/cloud_backend.py --strict` clean (the SDK ships types).
- Manual: `/research "best mechanical keyboard under $150"` with cloud on produces a comparison answer, picks validated substring-style against sources, citations repair still works. Then disable cloud, same question (with cache busted) produces a local answer with the same shape.
- `docs/research-architecture.md` updated with cloud synth subsection.
- CLAUDE.md updated with the one-liner.

## Parking lot
- **Sonnet escape hatch**: if Haiku underperforms on >6-source synth, user runs `/cloud model claude-sonnet-4-6`. No code change. 3× cost.
- **Prompt caching for the stable prefix**: could become worthwhile if the synth prompt grows past 4096 tokens (e.g. if we add few-shot examples or structured instruction blocks). Audit and add `cache_control` breakpoint at that point.
- **Cloud for `/ask` summarization**: `/ask` returns raw DuckDuckGo + Wikipedia text and does no synthesis. If we ever add an "explain this in plain English" post-stage, it'd be a candidate — same cost shape as research synth. Out of scope here.
- **Cloud for idle-tool rolls**: the in-character riff on a tool result is a small LLM call, but it fires constantly and would be a privacy-adjacent regression (the buddy's tone/persona leaks to the wire). Keep local.
- **Telemetry**: no cost/usage dashboard yet. If monthly spend needs watching, add a small counter in `memory.db` that increments `input_tokens` / `output_tokens` per cloud synth from `response.usage`. Future plan.
- **Managed Agents**: if `/research` ever grows into "go do this multi-step investigation and come back with a report," Managed Agents is the right primitive. Not today.
- **Cache the question-level answer on the cloud path too**: the existing research cache already does this transparently — it caches the final rendered answer, not the synth input. Nothing to do.
