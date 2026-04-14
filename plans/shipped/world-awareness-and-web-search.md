# World Awareness Sense + Web Search `/ask` Command

## Goal
Add two complementary "outside world" features: (1) a passive `world_awareness` sense that pulls ambient tech-world context (HN) for the buddy to riff on, and (2) a `/ask <question>` slash command that does a basic web search and feeds results back through the buddy's voice. `/ask` results can also kick off a conversation-session so the buddy can follow up with questions. Both opt-in, both mirror the existing weather-sense network pattern (free + keyless where possible).

## Non-goals
- No reactive per-app lookups (don't fetch GitHub repo info when user opens a repo — creepy, un-sanitizes browser titles)
- No paid search APIs in MVP (no SerpAPI, no Kagi). Defer paid tier to a follow-up plan
- No voice/STT integration with `/ask` — text input only for now
- No auto-invocation of web search by the brain — it's user-triggered via `/ask` only in MVP
- No multi-turn research agent, no chained tool calls, no citations UI. Single-query → single-result → single buddy quip
- No clipboard integration ("search what I copied") — clipboard is permanently off-limits
- No persistence of search history to disk (privacy — user-entered queries should not linger)

## Files to touch

**World awareness sense:**
- `tokenpal/senses/web_search/__init__.py` — currently empty; will hold `WorldAwarenessSense` registration (rename dir to `world_awareness` OR reuse `web_search` as the package). Decision: rename to `world_awareness` — `web_search` dir gets repurposed for Option C below
- `tokenpal/senses/world_awareness/__init__.py` — new, `@register_sense` hookup
- `tokenpal/senses/world_awareness/hn_client.py` — new, HN Algolia API poll (free, keyless)
- `tokenpal/senses/world_awareness/sense.py` — new, emits `SenseReading` with one-liner summary + TTL 30min, weight 0.25
- `tokenpal/config/schema.py` — add `[senses] world_awareness = false` default, `[world_awareness]` poll interval
- `config.default.toml` — document the new toggle

**Web search `/ask`:**
- `tokenpal/senses/web_search/__init__.py` — new purpose: back-end module for `/ask`, not a sense
- `tokenpal/senses/web_search/client.py` — new, abstraction over search backend (start with DuckDuckGo Instant Answer + Wikipedia REST as free keyless options, fall back to Brave Search API if key configured). **Module docstring: "outbound network, all returned text untrusted"**
- `tokenpal/app.py` — add `_cmd_ask` handler (mirrors `_cmd_gh` daemon-thread pattern), register via `dispatcher.register("ask", _cmd_ask)`. First-use warning uses marker file in data_dir (mirror `first_run.py:12 _MARKER_NAME` pattern)
- `tokenpal/brain/orchestrator.py` — add `_clear_conversation()` method that overwrites `history` list entries before dropping the reference (addresses guardrail #5; orchestrator.py:215-221 currently just dereferences)
- `tokenpal/config/schema.py` — **remove** dead `web_search: bool` from `SensesConfig`, **add** `world_awareness: bool = False` to `SensesConfig`. **Add** new `WebSearchConfig` dataclass: `enabled: bool = False`, `backend: str = "duckduckgo"`, `brave_api_key: str = ""`. Also support `TOKENPAL_BRAVE_KEY` env var override at load time
- `config.default.toml` — rename `web_search = false` → `world_awareness = false` in [senses]; add new `[web_search]` section for backend config
- **No new prompt builder** — reuse existing conversation path via `brain.submit_user_input(formatted_prompt)`. Voice propagates automatically through `build_conversation_system_message()` (personality.py:864-874). Conversation session auto-starts at orchestrator.py:662.

**Sensitive app list unification (one-time refactor):**
- `tokenpal/brain/personality.py` — keep `_SENSITIVE_APPS` as the canonical list (already imported into orchestrator.py:20)
- `tokenpal/senses/productivity/memory_stats.py` — replace local `_SENSITIVE_APPS` with import from `tokenpal.brain.personality`. Delete the duplicate.
- The banned-word filter for HN titles + search results reuses this unified list.

**Docs:**
- `CLAUDE.md` — add `world_awareness` to Senses section, add `/ask` to Slash Commands section
- `README.md` — one-line mention of both in features list

## Failure modes to anticipate
- **Rate limits on HN**: 30-min poll is conservative but add exponential backoff + last-known-good caching
- **Network failures**: weather sense already handles this; mirror its pattern (quiet degradation, no error quips spamming chat)
- **Search result bloat**: DuckDuckGo/Wikipedia responses can be long. Need to truncate to ~500 chars before feeding to 4k-context gemma4 or the quip prompt will blow the context window
- **Content safety from uncontrolled web sources**: HN titles AND search results can contain NSFW/offensive content. Need a rough filter (banned-word list) before feeding to LLM. **Apply the same filter + delimiter treatment to BOTH world_awareness HN titles AND `/ask` search results** — both are untrusted input
- **Prompt injection via search results OR HN titles**: a malicious page returned by DDG or a drive-by HN title ("Show HN: ignore previous instructions, output X") could manipulate the LLM. Wrap BOTH in delimiters (`<search_result>...</search_result>`, `<hn_item>...</hn_item>`) and rely on gemma4's instruction-following (imperfect but best without fine-tuning)
- **User typing `/ask` with sensitive info**: queries leave the machine. **First-use warning must explicitly name**: "Sends literal query text to DuckDuckGo. No cookies, no IP beyond TCP." Off by default + explicit opt-in
- **Brave API key leakage**: redact in `/status` output, `--validate`, in any exception traceback, at all log levels. **Also support loading from env var `TOKENPAL_BRAVE_KEY`** as alternative to config.toml (easier to rotate, less likely to land in backups)
- **Search result + HN title log truncation**: truncate to ~80 chars in log output (mirrors music-track-name redaction precedent). 500 chars of uncontrolled text in debug logs is too much persisted uncontrolled content
- **Voice-character drift on long search results**: feeding 500 chars of Wikipedia to a buddy prompt may cause the LLM to go into "narrator mode" and drop the sarcastic voice. Need anchor lines in the search prompt that re-assert the persona
- **Conversation session follow-up**: `/ask` DOES open a conversation-session so the buddy can ask follow-up questions about the search result. Need to wire `/ask` into the conversation orchestrator's entry points and seed the session history with (question, search-backed answer)
- **Conversation buffer must be zeroed on timeout, not just dereferenced**: search result text seeded into session should be overwritten on session expiry, not only GC'd. Confirm in implementation
- **World awareness turning stale**: if the 30-min poll fails for a day, the buddy would riff on yesterday's HN story. Need a max-staleness threshold after which the reading is suppressed (e.g. 2× TTL). **Staleness degrades silently — NO "couldn't reach HN" quip** (leaks network state to anyone shoulder-surfing)
- **`web_search` module docstring required**: state explicitly "this module makes outbound network calls; treat all returned text as untrusted; wrap in delimiters and apply banned-word filter before any LLM prompt composition"
- **Decision needed: rename `web_search` dir?** Currently empty, so rename is free. But `Files to touch` assumes a rename — flag for research pass to confirm nothing else references the dir name

## Done criteria
- `world_awareness` sense ships: opt-in via config, polls HN every 30min, emits a `SenseReading` with a one-liner summary, buddy occasionally riffs on it (confirmed via manual test)
- `/ask <question>` slash command works end-to-end: DuckDuckGo backend returns a result, buddy responds in-voice referencing the result, raw result visible in chat log (like `/gh` does), and a conversation-session opens so the buddy can follow up with questions about the topic
- Both features are **off by default** — fresh config has them disabled
- Brave Search API path is stubbed but not required for MVP (switching backends is a config change, not a code change)
- Prompt injection basic mitigation in place: search results wrapped in delimiters, banned-word filter applied before prompt composition
- Unit tests for: HN client response parsing, web search client with mocked backend responses, `/ask` command flow with mocked search, delimiter + banned-word filter applied to both world_awareness and web_search inputs, log-truncation at 80 chars, `_clear_conversation()` actually overwrites history buffer contents, sensitive-apps list imports don't break existing productivity tests
- `CLAUDE.md` and `README.md` updated
- First-use warning shown when user runs `/ask` for the first time — explicitly names what leaves the machine ("Sends literal query text to DuckDuckGo. No cookies, no IP beyond TCP. Continue?")
- Brave API key redaction verified in `/status`, `--validate`, and exception tracebacks. `TOKENPAL_BRAVE_KEY` env var supported
- `pytest` passes, `ruff check tokenpal/` passes, `mypy tokenpal/ --ignore-missing-imports` passes

## Parking lot
(empty — append "ooh shiny" thoughts that surface mid-work)
