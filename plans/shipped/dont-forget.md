# Don't Forget — persist chat log across restarts

## Goal
Persist the right-pane chat log to `memory.db` so restarting `tokenpal` hydrates the last N entries back into the chat-log widget instead of starting blank. Add an `OptionsModal` (the umbrella settings modal, built to grow) that exposes the history-size field (digit-only input, sanitized) and a "Clear history" button. Scaffolded so future rows can link out to existing modals (cloud, senses toggles, voice, etc).

## Non-goals
- Conversation-memory / multi-turn continuity across restarts. `ConversationSession` stays in-RAM; this is UX transcript recall, not LLM context.
- Full-text search over past chat. Append + tail-read only.
- Exporting the log to a file. `/chatlog export` is parking-lot, not this plan.
- Replaying speech bubbles or ASCII frames on startup. Hydration fills the scrollable chat log only.
- Cross-machine sync. The db is already per-machine.
- Backfilling observations that predate this feature.
- Deep changes to existing modals. Launcher buttons in `OptionsModal` will re-use the existing open paths (`/cloud` → `CloudModal`, `/senses` → `SelectionModal`). No refactor of `cloud_modal.py` or `selection_modal.py` on this pass.
- Voice-manager and model-browser launchers. Those slash commands (`/voice`, `/model`) dispatch to subcommands and don't have a single canonical modal-open path today. Parked.

## Files to touch
- `tokenpal/brain/memory.py` — new migration `_migration_3_chat_log` adding `chat_log` table (id, timestamp, speaker, text, url); new `MemoryStore.record_chat_entry()` + `get_recent_chat_entries(limit)` + `clear_chat_log()` helpers. Cap enforcement via trim-on-insert when row count exceeds `max_persisted * 1.5`.
- `tokenpal/ui/options_modal.py` (new) — `OptionsModal(ModalScreen[OptionsModalResult | None])`. Follows the `cloud_modal.py` shape: frozen `OptionsModalState` in, frozen `OptionsModalResult` out. Sections:
  1. **Chat history** — digit-only `Input` for `max_persisted` + a "Clear history now" `Button`. Sanitization via `Input(type="integer", restrict=r"[0-9]*", max_length=6)`. Belt-and-suspenders `_collect()` re-casts with `int()` in try/except and clamps to `[MIN, MAX]`.
  2. **Settings shortcuts** — launcher `Button`s for "Cloud LLM...", "Senses toggles...", and "Tools...". Each pressed button dismisses the modal with `OptionsModalResult(navigate_to="cloud" | "senses" | "tools", ...)`. The app-layer result handler sees `navigate_to` and calls the existing `/cloud` / `/senses` / `/tools` dispatch so no modal-opening logic is duplicated.
  Laid out as a `VerticalScroll` so future sections (voice, model, mood, etc) drop in as sibling `Container`s. `navigate_to` is a `Literal[None, "cloud", "senses", "tools"]` today — extending later is one string + one elif.
- `tokenpal/ui/base.py` — new messages: `OpenOptionsModal(state, on_result)`, `LoadChatHistory(entries)`, `ClearPersistedChatLog()`. Keep dispatch safe for console + tkinter overlays (noop handlers on the abstract base).
- `tokenpal/ui/textual_overlay.py` — (a) new `KEY_BINDINGS` entry for F3 "Options" that emits `OpenOptionsModal`; (b) `_append_log` calls a persist callback so every `_log_buddy` / `_log_user` row lands in `chat_log`; (c) on mount, if a history payload arrived, re-compose lines from `(speaker, text, url, ts)` and seed `_chat_log_lines` + `_link_urls` before any live line renders; (d) `on_clear_log` additionally dispatches `ClearPersistedChatLog`; (e) `on_open_options_modal` pushes the modal and hands the result back to the app layer.
- `tokenpal/app.py` — wire the F3 path AND the new `/options` slash command: build `OptionsModalState` from current config + live `MemoryStore`, open modal. Result handler branches: (a) `navigate_to == "cloud"` → re-dispatch to the existing `/cloud` bare-command handler (which already opens `CloudModal`); (b) `navigate_to == "senses"` → re-dispatch to `/senses` bare; (c) `navigate_to == "tools"` → call the existing `_open_tools_modal()` helper; (d) `navigate_to is None` → persist changes: rewrite `[chat_log]` via a new tiny `config/chatlog_writer.py` (mirrors `senses_writer.py`), and call `MemoryStore.clear_chat_log()` if the button was pressed. On startup, hydrate via `get_recent_chat_entries(cfg.chat_log.hydrate_on_start)` BEFORE the brain thread starts emitting.
- `tokenpal/config/schema.py` + `config.default.toml` — new `[chat_log]` section: `persist = true`, `max_persisted = 200`, `hydrate_on_start = 100`. Plus hard-coded `_MIN_PERSISTED = 0` (0 = effectively off) and `_MAX_PERSISTED = 5000` constants used by both the modal clamp and the writer validator.
- `tokenpal/config/chatlog_writer.py` (new) — targeted `[chat_log] max_persisted = N` upsert. Same `tomli_w` pattern as `senses_writer.py`.
- `tests/brain/test_memory.py` (or new `test_chat_log.py`) — migration applies cleanly, round-trip insert + tail read, clear wipes rows, trim-on-insert cap enforcement.
- `tests/ui/test_options_modal.py` (new) — digit-only restrict works, non-digit paste is rejected, clamp rejects out-of-range, Clear button propagates, Cancel returns None.

## Failure modes to anticipate
- **Privacy regression**: chat log excludes bubbles during sensitive-app windows already, but user-typed input CAN contain anything. Recommendation: persist `_log_user` verbatim (it's what the widget stores) and rely on 0o600 db + the "Clear history now" button. Double-check `/consent`-flow text doesn't flow through `_log_user`.
- **"No SQL injection" guardrail (user's explicit ask)**: every persisted value uses parameterized `?` placeholders, never string concat. Same rule applies in `clear_chat_log()` and in any writer. `max_persisted` is cast to `int` before any SQL touches it — the modal restricts at keystroke level AND the store validates again.
- **Input sanitization bypass via paste**: Textual's `Input(restrict=...)` blocks typed non-digits but pasted content can still land. Enforce by also setting `type="integer"` and re-validating in `_collect()` with `int()` inside try/except and an explicit `max(MIN, min(MAX, n))` clamp. An empty string after trim falls back to the current stored value, not 0.
- **Migration ordering**: `_MIGRATIONS` is append-only, currently length 2 (v0→v1 session_summaries, v1→v2 active_intent). Adding `_migration_3_chat_log` bumps `CURRENT_SCHEMA_VERSION` to 3. Confirm no other in-flight migrations.
- **Write amplification**: every bubble + every user message writes a row. Trim-on-insert only when row count exceeds `max_persisted * 1.5` so the delete doesn't run every insert.
- **Markup/URL round-trip**: `_append_log` composes a Rich-markup line with `[@click=app.open_chat_link("idx")]` where `idx` is a position in `_link_urls`. On hydrate those indices are stale. Must persist `(speaker, raw_text, url)` and re-compose the line on hydrate, not persist the rendered line.
- **Timestamp drift**: current lines show `"%I:%M %p"` only, no date. A hydrated row from yesterday would read "10:42 AM" with no context. Prefix hydrated rows with a date tag ("Apr 17 10:42 AM" if not today) to disambiguate.
- **Cap interaction with `_MAX_CHAT_LOG_LINES=500`**: hydration must respect that cap. If `hydrate_on_start > 500`, clamp.
- **`/clear` semantics**: Ctrl+L wipes RAM today. Extending it to wipe persisted rows is the right default but surprising — mention it in `/help`. The modal's "Clear history now" button is the discoverable version of the same action.
- **Overlays other than Textual**: console + tkinter overlays have no chat log and no modal. Hydration + F3 are noops there.
- **Startup ordering**: if the orchestrator emits observations before the overlay processes `LoadChatHistory`, history + live lines interleave. Post `LoadChatHistory` synchronously in `on_mount` (or before any brain thread starts).
- **Modal-result dispatch on the brain thread**: `push_screen(..., callback)` fires the callback on the UI thread. Writing config + calling `MemoryStore.clear_chat_log()` from there is fine but must be guarded against rapid repeat clicks.
- **Future-extension seams**: layout is a `VerticalScroll` of section `Container`s so adding "Voice" / "Model" launchers later is one more button + one elif branch in the result handler.
- **Navigate-then-dismiss race**: a launcher button dismisses the current modal BEFORE the next one opens, so briefly no modal is mounted. Post the re-dispatch via `call_after_refresh` from the result handler to avoid flicker or stuck focus on the buddy `Input`.
- **`/options` vs F3 parity**: both paths must hit the exact same dispatch helper. Don't fork the modal-open path between the keybinding and the slash command.
- **Modal-result dispatch on unsaved chat-history edits**: pressing a launcher button intentionally discards any typed `max_persisted` change (treated as cancel-for-that-field). Call this out in the section's help text so users aren't surprised.

## Done criteria
- New migration lands cleanly on fresh install AND on an existing db (pre-migration db → v2→v3 upgrade leaves prior data intact).
- After quitting and relaunching `tokenpal`, the chat-log pane shows the last `hydrate_on_start` entries with timestamps and working clickable URLs (verify by clicking a persisted `/ask` link).
- F3 AND `/options` both open `OptionsModal`. Typing letters in the history-size field does nothing (widget rejects keystrokes). Pasting `1; DROP TABLE chat_log` leaves the db intact — verified by a targeted test that calls `_collect()` on that input.
- "Clear history now" button empties the `chat_log` table and wipes the live widget.
- "Cloud LLM..." button closes `OptionsModal` and opens `CloudModal` at the exact same state `/cloud` would.
- "Senses toggles..." button closes `OptionsModal` and opens the same `SelectionModal` `/senses` would.
- "Tools..." button closes `OptionsModal` and opens the same modal `/tools` (`_open_tools_modal()`) would.
- `[chat_log] persist = false` disables writes entirely; existing rows stay untouched but no new ones land.
- `/clear` wipes both RAM and the persisted table.
- `pytest` passes, including round-trip + modal tests.
- `ruff check tokenpal/` + `mypy tokenpal/ --ignore-missing-imports` clean.

## Parking lot
- Voice modal: build a `VoiceModal` that surfaces every `/voice` subcommand (train, switch, list, off, info, finetune, finetune-setup, regenerate, ascii, import) as a discoverable UI, then add a "Voice..." launcher button to `OptionsModal` and extend `navigate_to` with `"voice"`.
