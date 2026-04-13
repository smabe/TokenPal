# Textual UI Rewrite

## Goal
Replace the hand-rolled console overlay (`console_overlay.py`) with a Textual-based overlay as the default UI. This fixes the input buffering bugs (stuck/repeated keys), adds a rich status bar with live sense data (#7), and gives us cross-platform terminal support including Windows — all from a proper TUI framework instead of raw ANSI + termios.

## Non-goals
- Removing ConsoleOverlay or TkOverlay — they stay as fallbacks
- Changing the brain, senses, or LLM layers — this is purely UI
- Conversation history widget (#8) — park for a follow-up plan
- Theming / color customization — ship defaults, iterate later
- Changing `AbstractOverlay` interface — the new overlay implements it as-is
- ASCII art redesign — reuse `BuddyFrame` and `SpeechBubble` unchanged

## Files to touch
- `pyproject.toml` — add `textual>=0.85` to dependencies
- `tokenpal/ui/textual_overlay.py` — **new file**, all widgets + `TextualOverlay(AbstractOverlay)`
- `tokenpal/ui/textual_overlay.tcss` — **new file**, CSS layout
- `tokenpal/config/schema.py` — change default overlay from `"console"` to `"textual"`
- `tokenpal/ui/registry.py` — update `resolve_overlay()` auto-detect to prefer textual
- `tokenpal/brain/orchestrator.py` — enrich `_push_status()` with weather/music/productivity data
- `tests/test_ui/test_textual_overlay.py` — **new file**, Pilot-based async tests

## Failure modes to anticipate
- `call_from_thread` before `app.run()` starts — brain thread fires callbacks during startup window
- Textual's `Input` widget may swallow keys that ConsoleOverlay forwarded (e.g. Ctrl+C for quit)
- Speech bubble auto-hide timers competing with new speech arrivals (cancel-before-set)
- Unicode width of ASCII buddy art — wcwidth differences between Textual/Rich and raw ANSI
- Status bar overflow on narrow terminals — need truncation strategy
- `app.exit()` from signal handler vs Textual's own signal handling — double-cleanup risk
- Sense data not yet available at startup — status bar must handle None/empty gracefully
- Typing animation timer interleaving with auto-hide timer (finish typing → then start auto-hide, not both)
- `schedule_callback` with delay > 0 needs Textual `set_timer`, delay == 0 needs `call_from_thread`

## Done criteria
- [x] `tokenpal` launches with Textual overlay by default, renders header/buddy/input/status bar
- [ ] Typing text into the Input widget works without stuck/repeated keys
- [x] Speech bubble appears with typing animation, auto-hides after display duration
- [x] Status bar shows: `mood | app | weather | music | spoke Xs ago` (omitting empty segments)
- [ ] `/help`, `/clear`, `/mood`, `/status`, `/model`, `/voice`, `/server`, `/zip` all work
- [ ] Brain thread comments arrive and render correctly via `call_from_thread`
- [ ] `overlay = "console"` in config.toml falls back to old overlay
- [x] `pytest` passes (existing + new tests)
- [x] `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean

## Parking lot

