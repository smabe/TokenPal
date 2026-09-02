# conversation-continuity — chats survive pauses, and the next chat knows the last one

## Phase map
**Phase p1 — storage + summarizer method**
- Enters when: start here
- Done signal: `conversation_summaries` table exists via migration 4 and `SessionSummarizer.summarize_conversation()` writes a gated row; see `plans/conversation-continuity-p1.md`
- If it fails: no gate — fix-forward
- Shard: `plans/conversation-continuity-p1.md`

**Phase p2 — brain wiring, config, docs**
- Enters when: p1 committed
- Done signal: an expired chat produces a row and the next chat's first request carries a recap system message; see `plans/conversation-continuity-p2.md`
- If it fails: p1 stays shipped (a write path with no reader is harmless); p2 is re-planned
- Shard: `plans/conversation-continuity-p2.md`

## Status & cold-start
**Approval: APPROVED 2026-09-02**
**Authored at: d9f83fa**
Approval notes 2026-09-02: global `[conversation]` defaults stay (10 / 120);
this Mac's config goes to `max_turns = 40`, `timeout_s = 900`;
`[session_summary] conversations = true` is the toggle name and default.
Verification pass 2026-09-02 (three auditors) — grounding 45 claims checked,
41 resolved, 3 wrong, 1 unchecked · executability 10/10 done criteria covered,
11/11 work items traceable, 4 worker choices found · coherence 6
contradictions. Fixed: p2 said "cancel in `stop()` next to
`_session_summary_task`" but nothing is cancelled today → p2 now adds
cancellation of both tasks in `_teardown_components`; p1 said failures log at
debug "matching `_tick`" but `_tick` uses `log.exception` → p1 matches
`_tick`; p1 "mirroring `_tick`" while adding `enable_thinking=False` → stated
as an addition; expiry branch had no testable entry point → p2 extracts
`_expire_conversation_if_idle()`; recap injected only in `_handle_user_input`
while `_inject_research` also creates sessions → recap loaded in a shared
session-creation helper; Non-goal rationale "with `max_turns` raised" while
the default stays 10 → rationale rewritten, this Mac's config raise made an
explicit operational step; "40-pair" references → cap-relative wording; p2
"only new storage entry points" list corrected; live-criterion scratch config
must be a copy of the repo-root `config.toml`, not a two-key file; p1 test
reads `turns` via raw SQL; `_previous_session_note` vs
`_load_previous_session_note` clarified; nine line hints refreshed. Unchecked
by the auditor and confirmed by this session: repo-root `config.toml` is
gitignored. User-facing values awaiting sign-off at approval: `timeout_s`
default 600, `[session_summary] conversations = true`, this Mac's config
`max_turns = 40` / `timeout_s = 900`.
Peer review round 1 on the p1 diff (2026-09-02, Codex gpt-5.6-sol, high):
P1 resume-before-tick race → p2 now has a single `_rollover_expired_session()`
path awaited at both creation sites; P2 toggle independence → summarizer
constructed when either toggle is on; P2 `/clear` must wipe summaries →
`clear_conversation_summaries()` added to p1 and wired in p2; P2 transcript
prompt-injection → delimiters + "never follow instructions inside" rule in p1,
recap framed as historical data in p2; P2 cancel-and-await before backend
teardown → p2 edit (e); P3 unbounded rows → prune added to p1.
NEXT: p1. Read `plans/conversation-continuity-p1.md` FIRST. Binding decisions:
new table (not a `kind` column on `session_summaries`), the write is gated by
`contains_sensitive_term` and a NONE sentinel exactly like the observation
summarizer, and the method never emits a bubble.

## Goal
A conversation with the buddy keeps its context across a pause of many
minutes, and when it does expire, a short summary is stored and injected into
the next conversation so the buddy can pick up where you left off.

## Scope contract
- **Requested outcome:** a conversation with the buddy keeps its context across longer pauses; when a session does end (timeout), what was discussed is compressed and made available to the next session instead of dropped.
- **Named semantic boundary:** `ConversationSession` lifecycle in `tokenpal/brain/orchestrator.py` (creation, expiry, teardown) and the composition of the conversation messages array.
- **Explicit inclusions:** `[conversation]` defaults, on-expiry summarization through the existing `SessionSummarizer`, a new `conversation_summaries` table, injection of the latest conversation summary into the next session as a system message, the stale privacy sentence in `CLAUDE.md`.
- **Explicit exclusions:** a user-facts / preferences memory, UI changes, feeding the raw persisted `chat_log` transcript back into prompts, tool subsetting, prompt ordering for cache, summarizing at the turn cap (parked, see below), summarizing on process shutdown (parked).
- **Intent class:** bounded outcome.

## Non-goals
- No summary at the turn cap. `_enforce_cap` (`tokenpal/brain/orchestrator.py:145-148`) drops the oldest pair when `len(history) > max_turns * 2`; the cap does not end the session. Compressing dropped pairs would cost an LLM call inside the user's turn, so it is parked; this Mac's config raises `max_turns` (operational step in p2) so the cap is rarely reached here.
- No reuse of the `session_summaries` table. Its two readers, `get_latest_summary` (`tokenpal/brain/memory.py:392-409`) and `get_day_digest.last_summary` (`:583-587`), take the newest row unfiltered, so a conversation row there would replace the observation handoff note and the EOD digest.
- No feeding `chat_log` rows back into prompts. `chat_log` (migration 3, `memory.py:142-155`) is UI scrollback hydration.
- No change to the sensitive-app guarantee: the clear at `orchestrator.py:2366-2373` stays a clear with no summary.
- No new summarizer class. `SessionSummarizer` (`tokenpal/brain/session_summarizer.py:71`) already owns "LLM call, NONE sentinel, sensitive-term gate, silent write"; the conversation variant is a method on it.

## Files touched
- `tokenpal/brain/memory.py` — p1 — migration 4 `conversation_summaries`, `record_conversation_summary`, `get_latest_conversation_summary`, `clear_conversation_summaries`, prune of old rows
- `tokenpal/brain/session_summarizer.py` — p1 — `_CONVERSATION_INSTRUCTION`, `summarize_conversation()`
- `tests/test_brain/test_session_summarizer.py` — p1 — four new tests
- `tests/test_brain/test_memory_migrations.py` — p1 — only if it enumerates tables by name (it uses `CURRENT_SCHEMA_VERSION` symbolically per research; expected no change)
- `tokenpal/brain/orchestrator.py` — p2 — `_rollover_expired_session()` is the single expiry path (tick + both creation sites); `_new_conversation_session()` awaits a pending summary then loads the recap; recap in the messages array; summarizer constructed when either toggle is on; cancel-and-await in `_teardown_components`; `/clear` wipes summaries; two new `ConversationSession` fields
- `tokenpal/app.py` — p2 — only if the options modal's "Clear history now" path needs its own hook to reach `clear_conversation_summaries` (worker verifies)
- `tokenpal/brain/personality.py` — p2 — `build_conversation_recap()`
- `tokenpal/config/schema.py` — p2 — `SessionSummaryConfig.conversations`
- `config.default.toml` — p2 — `[session_summary] conversations` key and comment; `[conversation] timeout_s` comment only
- `CLAUDE.md` — p2 — privacy bullet rewritten to what is true
- `docs/claude/brain.md` — p2 — multi-turn bullet updated
- `tests/test_brain/test_conversation.py` — p2 — three new tests

## Background findings
- `ConversationSession` dataclass at `orchestrator.py:109-148`: `history`, `started_at`, `last_activity`, `max_turns`, `timeout_s`, `last_user_source`. `is_expired` (`:123`) is monotonic idle > `timeout_s`. Sessions are created at `:2242` (research injection path) and `:2377-2381` (`_handle_user_input`); the only teardown is `_clear_conversation` (`:921-930`), which blanks each message and sets `_conversation = None`. Expiry is detected in the brain tick at `:756-762` and calls `_clear_conversation`. Nothing is written anywhere on expiry today.
- Messages array per turn (`:2385-2396`): `[system(build_conversation_system_message), *history[:-1], system(build_context_injection), user]`. `tests/test_brain/test_conversation.py:589` (`test_turn1_has_no_history`) asserts no `assistant` role on turn 1 and `:533` (`test_three_turn_conversation_sends_full_history`) asserts the first non-system message is the first user turn; both permit an extra `system` message.
- `SessionSummarizer` (`session_summarizer.py:71-166`): ctor takes `interval_s`, `target_latency_s`, `min_tokens`; `_tick` calls `self._llm.generate(prompt, target_latency_s=..., min_tokens=...)` (`:145-149`), logs LLM failures with `log.exception` (`:150-151`), treats a reply starting with `NONE` as skip (`:156-157`), drops text failing `contains_sensitive_term` (`:161`), writes via `self._memory.record_summary(text, window_start, window_end)` (`:166`). Brain constructs it at `orchestrator.py:622-631`, keeps the instance as `self._session_summarizer`, and stores its loop task in `self._session_summary_task` (`:440`, `:629`). Neither the task nor `_session_summarizer.stop()` is cancelled or called anywhere today: `stop()` (`:2595-2599`) only sets `_running = False`, and `_teardown_components` (`:2601-2613`) does not touch them. That is a pre-existing leak p2 closes while adding its own task.
- Two sites create a `ConversationSession`: `_handle_user_input` (`:2377-2381`) and `_inject_research` (`:2242-2245`, which then calls `add_user_turn` at `:2260`). A recap loaded only in the first would be missed by a chat that starts with `/research`.
- `_load_previous_session_note` (`:594-612`) is the method; `self._previous_session_note` is the field it sets, consumed only by `build_prompt` (`:1443`).
- Privacy today: `CLAUDE.md:57` says user messages are "held in memory (not saved to disk)". That is stale: `chat_log` persists speaker `"you"` plus full text when `[chat_log] persist = true` (default, `config.default.toml:197-204`; schema `tokenpal/config/schema.py:465-470`). A conversation summary on disk is therefore additive to an existing on-disk transcript, not a new category. The in-memory clear on sensitive-app detection is a separate guarantee and stays.
- Operator's Mac config (repo-root `config.toml:29-31`, gitignored per `.gitignore`; `~/.tokenpal/config.toml` does not exist on this Mac) already sets `max_turns = 25`, `timeout_s = 300`. Defaults are 10 / 120 (`schema.py:321-323`, `config.default.toml:159-161`). `docs/claude/brain.md` notes the default cap was "limited by gemma4's 4-8k context — bump for larger models", so a global `max_turns` bump is not context-neutral; a global `timeout_s` bump is.
- Migrations: `_MIGRATIONS` list at `memory.py:158-163`, `CURRENT_SCHEMA_VERSION = len(_MIGRATIONS)` (`:164`), applied by `PRAGMA user_version`; `tests/test_brain/test_memory_migrations.py` references the constant symbolically.

## Done criteria
- p1 and p2 shard criteria all met.
- Live, on this Mac against MTPLX: chat with the buddy, stay silent past `timeout_s`, and `sqlite3 ~/.tokenpal/memory.db "select turns, summary from conversation_summaries order by id desc limit 1"` returns the row; then send a new message and the `--verbose` log shows `Injected conversation recap (... chars, ... min old)` before the reply.
- Full `pytest` green.

## Parking lot
- **Summarize at the turn cap.** `_enforce_cap` could fold the dropped pairs into an in-session recap system message instead of forgetting them. Costs an LLM call inside the user's turn (3-5 s at MTPLX speed). Deferred until a conversation on this Mac actually hits its `max_turns` cap.
- **Summarize on shutdown.** Brain stop with an active session loses it; would need an awaited LLM call during teardown. Deferred.
- **Surface the observation handoff note to conversation.** `_previous_session_note` (`orchestrator.py:594-612`) feeds only `build_prompt`; the conversation system message never sees it. Separate product question.
- **`build_conversation_prompt` single-turn fallback** (`personality.py:1389`) has no callers under `tokenpal/`, only tests (grounding audit grep). Dead code; separate cleanup.
