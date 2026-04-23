# smarter-buddy

## Goal
Let users ask cheap follow-up questions on cloud research results (Haiku / Sonnet / Opus via `/cloud synth` or `/cloud search`). Today a `/research` call on a niche topic costs ~$0.20 via `/cloud search`, and any follow-up requires a brand new $0.20 call that loses all prior grounding. After this plan: simple follow-ups are free (answered by the ambient conversation LLM from prior context), and escalated follow-ups re-engage the same Anthropic message history with prompt caching — ~$0.02-0.05 instead of $0.20, and Sonnet keeps its grounding instead of re-searching from scratch.

**Design: A + B from the design-space sketch.**
- **B (default)**: The last research answer is already in the ambient conversation LLM's rolling context; for simple follow-ups it just answers from context. No new tool call, no cost.
- **A (escalation)**: When the user needs info the prior answer doesn't cover, the conversation LLM calls a new `research_followup` action. That action reloads the persisted Anthropic message history for this chat session, appends the user's follow-up as a new turn, and re-sends with a `cache_control` breakpoint on the cached prefix. Sonnet either reasons over prior sources or issues new `web_search` / `web_fetch` calls on top of them.

## Non-goals
- **Parked entirely (separate future plan): local-path improvements** — GitHub Issues + Reddit backends, `kind=troubleshooting` synth, staged search, planner intent routing for debugging questions. Valid work, but orthogonal to follow-ups and out of scope here. See parking lot.
- No changes to the local research pipeline shape, backends, or synth kinds.
- No Qt/Textual UI changes. The follow-up is triggered by the conversation LLM picking a tool; the user types normally into chat.
- No new slash command beyond `/followup` as an explicit override. No `/continue`, `/deeper`, etc.
- No multi-session memory — follow-up state lives in-process, TTL-scoped, lost on restart. Persisting across restarts is deferred.
- No sensitive-content gating rework. The cached message history contains only what was already sent on the first call; nothing new crosses the wire that didn't before.
- No cost estimator UI. Cost appears in the telemetry line; we don't build a pre-call price display.
- No extension to the local `/refine` path. That path reuses cached excerpts and is already scoped narrowly; we're not widening it to cloud modes (empty excerpts problem, already documented in research-architecture.md).

## Files to touch
- `tokenpal/brain/research.py`
  - New `ResearchSession` dataclass: `sources: list[Source]`, `original_prompt: str`, `answer_text: str`, `messages: list[dict] | None` (only populated for `mode="deep"`), `tools: list[dict]`, `model: str`, `mode: Literal["synth", "search", "deep"]`, `total_cost_usd: float`, `followup_count: int`, `created_at: float`, `last_used_at: float`. **No `chat_session_id` — scoping is via `Brain._active_research_session` slot (Option 2).**
  - New helper `_build_messages_from_synth_result(session: ResearchSession) -> list[dict]` — reconstructs `[{user: original_prompt}, {assistant: answer_text}]` for synth/search modes (since `CloudBackend.synthesize()` is one-shot and doesn't retain a message list). Deep mode uses `session.messages` directly.
  - After a successful cloud `run()` / `run_deep()` (insert points `research.py:327` and `research.py:827`), build a `ResearchSession` and hand it to the orchestrator for storage on `Brain._active_research_session`.
  - New `run_followup(question: str, session: ResearchSession) -> ResearchAnswer`: checks TTL + `followup_count < max_followups`, dispatches to `CloudBackend.followup(session, question)`, updates session in place (append turns to `messages` for deep; for synth/search, append to a reconstructed list each call), returns rendered answer.
- `tokenpal/brain/orchestrator.py`
  - `Brain` gains `self._active_research_session: ResearchSession | None = None` (init'd in `__init__`).
  - After a successful cloud `/research` completes, orchestrator stashes the built `ResearchSession` into that slot. Only one active at a time — newer research overwrites older. TTL-expire lazily at read time based on `last_used_at`.
  - `_wire_action_dependencies()` (lines 707-724) injects `_brain_ref` (or a narrow getter) into `ResearchFollowupAction` so it can reach the slot at execute time.
- `tokenpal/llm/cloud_backend.py`
  - New `CloudBackend.followup(session: ResearchSession, new_user_turn: str) -> LLMResponse | CloudBackendDeepResult`: rebuilds the `messages.create` call using `_build_messages_from_synth_result` or `session.messages` depending on `session.mode`, appends `{"role": "user", "content": new_user_turn}`, places `cache_control: {"type": "ephemeral"}` on the **last content block of the second-to-last message** (the prior assistant turn). Keeps the same `tools`, `model` the original call used. Reuses the pause-turn resume loop at `cloud_backend.py:319-409` for follow-ups that trigger tool calls.
  - Extend `CloudBackendError` (`cloud_backend.py:68-82`) with new `kind` values: `"no_session"`, `"expired"`, `"over_cap"`.
- `tokenpal/actions/research/research_action.py`
  - New `@register_action` class `ResearchFollowupAction` (`action_name = "research_followup"`) with tool schema `{"question": str}`. Gets a `_brain_ref` attribute injected by the orchestrator (mirroring `_llm`, `_memory` pattern at `orchestrator.py:707-724`).
  - Action reads `self._brain_ref._active_research_session`, dispatches to `runner.run_followup`, wraps result in `<tool_result>` with telemetry showing `followup=N/<cap>`, `cost_usd_delta`, `cache_read_tokens`.
  - On `no_session` / `expired` / `over_cap`: return a short tool_result telling the conversation LLM to either do a fresh `/research` or answer from context. Don't crash.
- `tokenpal/app.py` (NOT `slash.py` — slash commands are hardcoded methods here, pattern at `app.py:1080-1092` for `/refine`)
  - New `_cmd_followup(args: str)` method mirroring `_cmd_refine`. Calls a new `brain.submit_followup_question(args)`.
- `tokenpal/brain/personality.py`
  - `_tool_use_rule` (`personality.py:1325-1354`) gets a new rule inserted after rule 7: "Before calling `research_followup`, check: does the prior `<answer>` block already contain the information the user is asking about? If yes, answer directly from context. Call `research_followup` only if the user needs NEW information (new symptom, edge case, deeper detail) the prior answer doesn't cover. Never call `research_followup` twice in a row without the user speaking."
- `tokenpal/config/schema.py`
  - `ResearchConfig` gains: `followup_enabled: bool = True`, `followup_ttl_s: int = 900`, `followup_max_per_session: int = 5`, `followup_cache_breakpoints: bool = True`.
- `docs/research-architecture.md` — new section "Follow-ups on cloud research" covering: when B vs A fires, `ResearchSession` lifetime, cache_control placement, cost math, failure modes, telemetry additions. Clarify: follow-up scope is one active session per Brain, overwritten on next `/research`, no chat-conversation tie.
- Tests:
  - `ResearchSession` TTL expiry + cap enforcement (pure dataclass + helpers, fast unit tests).
  - `_build_messages_from_synth_result` determinism — same session produces byte-identical prompt across calls (cache-stability guard).
  - `run_followup` happy path against a mocked CloudBackend — asserts the outbound message list equals reconstructed history + new user turn, asserts `cache_control` lands on the right block.
  - `run_followup` error paths: no session on Brain, expired session, over cap, cloud auth failure.
  - Personality prompt update doesn't regress existing `/research` tool-calling behavior on a comparison fixture.

## Failure modes to anticipate
1. **Cache misses silently eating budget.** Anthropic's prompt cache is opaque — a misformed `cache_control` block, a prefix drift between calls, or even an SDK version skew can turn a "cheap" follow-up into a full-price re-send. Mitigation: log the `usage.cache_read_input_tokens` and `usage.cache_creation_input_tokens` on every follow-up response; telemetry flags follow-ups where cached tokens < 50% of prior-prefix size as a warning. Config kill-switch `followup_cache_breakpoints=false` to disable caching if it's misbehaving.
1a. **Prompt reconstruction drift (synth/search mode).** For non-deep mode, the saved user turn is the formatted `_SYNTH_PROMPT` with `sources_block` — research.py:672-674. If excerpts get reformatted, retruncated, or re-filtered between calls, the reconstructed prompt won't byte-match the original and prompt caching silently misses. Mitigation: `_build_messages_from_synth_result` rebuilds the prompt deterministically from saved `sources` (no re-fetching, no re-filtering), and we snapshot the formatted prompt string verbatim on the session.
1b. **Tool list pinning.** Deep-mode tools list at `cloud_backend.py:286-298` is built from `include_fetch`. A follow-up that reuses a "search" session but lets Sonnet invoke `web_fetch` breaks schema. Mitigation: save `tools` on the session, re-send that exact list on follow-up, no substitution.
2. **Conversation LLM over-escalates.** If the rule is too loose, the LLM calls `research_followup` for questions it could answer from context, burning cost for no reason. Mitigation: rule wording is strict (see personality.py bullet); anti-loop guard; hard cap `followup_max_per_session=5` per session.
3. **Conversation LLM under-escalates.** Opposite failure — user asks "what else?", LLM guesses from its own memory instead of re-engaging research. Mitigation: `/followup <question>` slash as explicit override; rule phrasing picks up "else/other/more/what about" cues.
4. **`chat_session_id` plumbing.** `research_followup` needs to know which conversation this is. If we don't already thread a session id through tool calls, we invent one here. Risk: misaligned sessions → wrong history loaded. Research pass must confirm how the action layer currently identifies conversations.
5. **Schema drift between initial and follow-up calls.** The first call was `/cloud search` (synth schema + web_search tool). If we re-send with the same `tools=[web_search]` but Sonnet decides the follow-up doesn't need search, that's fine — tool use is optional. But if the model invokes `web_fetch` in a follow-up on a `search`-only session, that's a schema mismatch. Mitigation: preserve the exact `tools` list saved with the session.
6. **Saved history grows unboundedly.** Each follow-up appends turns; after 5 follow-ups, context can exceed model window. Mitigation: enforce `followup_max_per_session`, and in telemetry log the cumulative input token count so we catch runaway growth before it 400s.
7. **Anthropic SDK model deprecation.** Session saved with model `claude-sonnet-4-6`; user swaps to `claude-opus-4-7` mid-session via `/cloud model`. The saved session still references the old model. Policy: follow-ups reuse the model the initial call used — session is effectively pinned. Document this; don't silently swap.
8. **TTL expiration race.** User asks follow-up 14:58 after research, call arrives at 15:02. Should it fail or extend? Policy: check TTL at the start of `run_followup`; if expired, return `"expired"` with a suggestion to rerun `/research`. No sliding-window renewal (keeps cost bounded).
9. **`/cloud deep` follow-ups are pricey.** Deep mode is $1-3/run; even a cached follow-up could be $0.30+ if Sonnet decides to re-fetch pages. Mitigation: telemetry explicitly labels deep-mode follow-ups with the cost delta so the user sees what they're spending. No cap beyond `followup_max_per_session` since the user chose deep mode intentionally.
10. **Concurrent follow-ups on the same session.** Unlikely in single-user TokenPal, but if the brain fires two tool calls at once, history mutation races. Mitigation: asyncio lock on `ResearchSession` instances.

## Done criteria
1. After a `/cloud synth` or `/cloud search` `/research` call (e.g., the Immich question), typing a natural follow-up in chat ("I already tried the thumbnails regen, what else?") results in the conversation LLM either answering directly from context (cheap path B) OR calling `research_followup`, producing an updated answer that references the prior sources without a fresh search fan-out. Verified manually on a live run.
2. Cache telemetry: the follow-up's log line shows `cache_read_input_tokens > 50% of prior prefix size` on at least one end-to-end test. Caching is actually working, not silently bypassed.
3. Cost measurement: a follow-up on a `/cloud search` session costs ≤ $0.07 (target ~$0.02-0.05, allow headroom). Measured from the Anthropic response's `usage` on a real call. Documented in the plan's parking lot for reference.
4. `/followup <question>` slash command works as an explicit override. Works even if the conversation LLM didn't escalate automatically.
5. Error paths handled without crashing: no saved session, expired session, over-cap session. Tool-result returned in each case, conversation LLM gracefully routes the user to `/research`.
6. `docs/research-architecture.md` has a "Follow-ups on cloud research" section covering A vs B, `ResearchSession`, cache_control placement, cost math, telemetry. `pytest`, `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` all pass.
7. No regression: a standalone `/research` call (no follow-up) produces byte-identical output to before the change. Session persistence is additive; the render path is untouched.

## Parking lot
- **Deep-mode memory retention (post-Phase-1 optimization, surfaced in efficiency review)**: `FollowupSession.messages` for deep mode stores the full pause-turn loop history including `tool_result` blocks with fetched HTML bodies from `web_fetch`. ~50KB × 5 sources = 250KB+ retained for the 15-minute TTL per active session. Not a leak (single slot, overwritten on next /research), but a real steady-state cost if deep mode is used frequently. Future optimization: strip non-final tool_result blocks or swap to text excerpts before stashing.
- **Local-path improvements (split out to a separate plan when we come back to it)**: add `github_issues` + `reddit` backends in `tokenpal/senses/web_search/`, `kind=troubleshooting` synth in `tokenpal/brain/research.py` (`SYNTH_SCHEMA`, `_SYNTH_PROMPT`, validators, render), planner intent routing for debugging queries, staged search that only fires Tavily on explicit planner picks. Motivation unchanged: the Immich-class question chokes on the local path because 4/5 of the useful sources live on `github.com/immich-app` which the planner can't reach. Plan lives on — write as `plans/smarter-local-research.md` when we start it.
- Cross-restart session persistence (pickle / sqlite) if users want follow-ups to survive a tokenpal restart.
- Automatic session summarization when `followup_count` approaches the cap (compact old turns into a summary message to make room for more follow-ups without blowing the context window).
- Slash UI polish: show follow-up count + remaining cap in the chat header.
- Reference run output: the Immich `/cloud search` answer from 04:02 PM (113s, 2448 tokens, 5 sources — 4 github.com/immich-app URLs) lives in `grand-plan-resume.md` style sidebar for manual verification of done criteria #1.
