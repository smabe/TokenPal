# Hot off presses — news history window

## Goal
Surface a Qt window that shows every news headline picked up by the three
world-news senses (`lobsters`, `github_trending`, `world_awareness`) as they
arrive, in-memory only, with clickable URLs. Toggleable from the tray.

## Non-goals
- No persistence. No SQLite, no `~/.tokenpal/news.json`, no append-on-shutdown.
  Buffer lives in process memory and dies on quit. (Explicit user ask.)
- No new sense, no scraping, no extra HTTP polling. We tap the readings the
  three existing senses already emit.
- No filtering UI (toggle by source, search, mark-read). Maybe parking-lot.
- No headline summarization / LLM rewriting — show the raw title verbatim.
- No Textual / console parity. Qt only this round.
- No "unread" badge on the tray icon.

## Files to touch
- `tokenpal/brain/news_buffer.py` — NEW. `NewsItem` dataclass +
  `NewsBuffer` (deque, URL-keyed dedupe, `extract_from_reading()` that
  maps each of the three sense schemas into `NewsItem`s).
- `tokenpal/brain/orchestrator.py` — call `news_buffer.extract_from_reading`
  on each poll reading whose `sense_name` is one of the three news senses;
  forward newly added items to `overlay.add_news_items(...)`.
- `tokenpal/ui/base.py` — declare `AbstractOverlay.add_news_items` /
  `toggle_news_history` as no-op defaults so non-Qt overlays don't crash.
- `tokenpal/ui/qt/news_window.py` — NEW. `NewsHistoryWindow` mirroring
  `ChatHistoryWindow`'s frameless / drag-handle / glass styling but with
  one row per item (source badge · clickable headline · small meta line).
- `tokenpal/ui/qt/overlay.py` — instantiate the window in `setup()`,
  implement `add_news_items` + `toggle_news_history`, drain pending
  buffer at mount time, wire tray callback.
- `tokenpal/ui/qt/tray.py` — add "Show news" / "Hide news" toggle
  action between chat-log and Options.
- `tokenpal/ui/qt/app.py` — pass a no-op `on_toggle_news` so the
  pre-brain shell still constructs the new tray correctly.
- `tests/test_brain/test_news_buffer.py` — NEW. extraction for all three
  sense shapes, URL dedupe across polls, deque cap behaviour, missing-URL
  fallback.
- `tests/test_ui/test_news_window.py` — NEW. smoke: window renders an
  item, source badge present, URL becomes a clickable link.

## Failure modes to anticipate
- **Three heterogeneous sense data shapes.** `lobsters`/`world_awareness`
  emit `data["stories"]` (HN uses `points`, lobsters uses `score`);
  `github_trending` emits `data["repos"]` with `full_name` + `stars`. A
  naive `data["stories"]` extractor silently drops github_trending. Pin
  the extraction with one branch per `sense_name`, and a test per shape.
- **Re-poll duplicates.** Each sense already dedupes its own *summary*
  via `_prev_summary`, but if one of three headlines changes the whole
  summary differs and the other two re-emit. Dedupe in the buffer by URL.
- **Missing URL.** HN self-posts can land with empty `url`. If we key on
  URL alone, two distinct self-posts collapse. Fall back to
  `(source, title)` as the dedupe key when url is empty.
- **Buffer unbounded.** Three senses · ~3 items · poll every 15min for
  hours → only ~tens-hundreds, but bound it anyway. `deque(maxlen=200)`.
- **macOS frameless focus.** Per
  `project_qt_frameless_focus.md`, the news window needs `activateWindow()`
  alongside `raise_()` after `show()`, and the toggle path must persist
  visibility intent the same way `_history_user_visible` already does
  (see how chat history is wired). Don't invent a new pattern.
- **Pre-setup ordering.** Brain emits `add_news_items` from its first
  poll, possibly before `QtOverlay.setup()` has built the window. Use the
  same `_pending_*` buffer-and-drain pattern `load_chat_history` uses.
- **Overlay-API drift.** `AbstractOverlay` has many methods; non-Qt
  overlays (textual, tkinter, console) inherit no-ops. Add the new
  methods to the base class with no-op defaults so the orchestrator can
  call them unconditionally.
- **Sensitive-content filtering.** Senses already filter via
  `contains_sensitive_content_term` before emitting, so the buffer only
  sees clean items — but a future sense added to the trio would bypass
  this if we don't centralise. Note: out-of-scope for this plan, just
  flag it in a code comment if relevant.
- **Tray menu growth.** Adding a third toggle pushes Options/Quit down.
  Group buddy/chat/news together with one separator, keep Options + Quit
  below their existing separators.
- **Cleared chat history ≠ cleared news.** User running `/clear` wipes
  the chat log; news buffer should be unaffected. Explicit assertion in
  the test for whoever wires `clear_log`.

## Done criteria
- Tray menu has "Show news" / "Hide news" toggle that opens / closes a
  frameless news window with the same look-and-feel family as the chat
  history window.
- All three news senses' headlines land in the window as they're polled,
  one row per headline, source label visible, URL clickable.
- Re-polling the same headline does not produce a duplicate row.
- Closing and reopening the window preserves the in-memory list (no
  reload needed) AND nothing is written to disk during the session.
- `pytest tests/test_brain/test_news_buffer.py tests/test_ui/test_news_window.py`
  passes.
- Full `pytest` suite green (per CLAUDE.md SOP).
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.

## Parking lot
- Extract a `TranslucentLogWindow` base class (or `TranslucentLogStyle`
  helper) shared by `ChatHistoryWindow` and `NewsHistoryWindow`. Real
  duplication: `paintEvent` / `set_background_*` / `set_font_color` /
  `_rebuild_background_brush` / `_apply_log_stylesheet` / `_trim_to_cap`
  are ~95% identical between the two windows, ~55 lines total. Surfaced
  by phase-2 simplify pass; deferred because it's a separate refactor.
- Wire news-window opacity / bg / font-color setters through
  `AbstractOverlay` + `OptionsDialog` so the news window shares the
  chat panel's user-tunable styling. Currently the methods exist on
  `NewsHistoryWindow` but no UI plumbs them.
