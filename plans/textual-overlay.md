# Plan: Textual Overlay for TokenPal

## Context

The console overlay (`tokenpal/ui/console_overlay.py`) uses a hand-rolled render loop with raw ANSI codes, termios cbreak mode, and `select.select()` for input. It works but has limitations: full-screen clear on every frame (no delta rendering), no Windows input support (termios is Unix-only), and manual thread-safe callback plumbing. Textual (by Textualize, 35k stars) gives us CSS layout, built-in animation/timers, native async, `call_from_thread()`, and cross-platform terminal support including Windows — all for free.

**Goal:** Add a new `TextualOverlay` that implements `AbstractOverlay`, make it the default, and keep `ConsoleOverlay` + `TkOverlay` as fallbacks.

---

## Files to Create

### `tokenpal/ui/textual_overlay.py`
Single file containing all Textual widgets and the overlay class (same pattern as `console_overlay.py`).

**Custom message:**
- `AutoHideSpeech(Message)` — posted when the auto-hide timer fires

**Widgets (all subclass `Static`):**

| Widget | Purpose | Key behavior |
|--------|---------|-------------|
| `HeaderWidget` | Centered buddy name with `─` borders | Recalculates border width on resize |
| `SpeechBubbleWidget` | Speech bubble with typing animation | `start_typing(bubble)` → `set_interval(0.03)` advances index, creates partial `SpeechBubble(text[:i]).render()`, updates content. On completion: `set_timer(max(10, len*0.15))` for auto-hide. Hidden via CSS `display: none` when inactive |
| `BuddyWidget` | ASCII buddy art | `show_frame(frame: BuddyFrame)` joins `frame.lines` with newlines |
| `StatusBarWidget` | Bottom status text | `set_text(text: str)` updates content |

**`TokenPalApp(App)`:**
```
compose() yields:
  HeaderWidget
  Spacer()          <- pushes content to bottom
  SpeechBubbleWidget
  BuddyWidget
  Input(placeholder="Type a message or /command...")
  StatusBarWidget
```
- `on_input_submitted()` — routes to `_input_callback` or `_command_callback` based on `/` prefix
- `on_auto_hide_speech()` — calls overlay's `hide_speech()` logic
- `BINDINGS = [("ctrl+c", "quit")]`

**`TextualOverlay(AbstractOverlay)`:**
- `overlay_name = "textual"`, `platforms = ("windows", "darwin", "linux")`
- Decorated with `@register_overlay` — autodiscovery in `registry.py` picks it up

**Method mapping:**

| AbstractOverlay | Textual implementation |
|---|---|
| `setup()` | Create `TokenPalApp(self)` instance |
| `show_buddy(frame)` | `call_from_thread` -> `BuddyWidget.show_frame(frame)`, update frame to "talking" |
| `show_speech(bubble)` | `call_from_thread` -> show widget, `SpeechBubbleWidget.start_typing(bubble)` |
| `hide_speech()` | `call_from_thread` -> cancel timers, hide widget, buddy -> idle |
| `update_status(text)` | `call_from_thread` -> `StatusBarWidget.set_text(text)` |
| `set_input_callback(cb)` | Store on `self._input_callback` |
| `set_command_callback(cb)` | Store on `self._command_callback` |
| `run_loop()` | `self._app.run()` (blocks main thread) |
| `schedule_callback(cb, delay)` | `call_from_thread(cb)` if delay==0, else `call_from_thread(lambda: set_timer(delay/1000, cb))` |
| `teardown()` | `self._app.exit()` |

**Thread safety:** `call_from_thread()` replaces the lock-protected callback queue entirely. Guard with `is_running` check to handle early calls before `run()` starts.

### `tokenpal/ui/textual_overlay.tcss`

```css
Screen { layout: vertical; background: #1a1a2e; }
HeaderWidget { dock: top; height: 3; content-align: center middle; color: #00ff88; text-style: bold; }
SpeechBubbleWidget { height: auto; max-height: 50%; content-align: center middle; color: #dcdcdc; padding: 0 2; display: none; }
BuddyWidget { height: auto; content-align: center middle; color: #00ff88; padding: 1 0; }
Input { dock: bottom; margin: 0 1; background: #1a1a2e; color: #dcdcdc; border: tall #333333; }
StatusBarWidget { dock: bottom; height: 1; color: #666666; padding: 0 2; }
```

`Spacer` between `HeaderWidget` and `SpeechBubbleWidget` eats vertical space -> bottom-anchored layout.

### `tests/test_ui/test_textual_overlay.py`

Using Textual's `app.run_test()` + `Pilot` (async, no real terminal needed):
- Header renders buddy name
- Buddy widget shows idle frame, switches on `show_frame()`
- Speech bubble typing animation completes after `len * 30ms`
- Auto-hide fires after display duration
- Input dispatches text to `input_callback`
- Input dispatches `/commands` to `command_callback`
- `schedule_callback` executes from another thread
- `teardown()` exits cleanly

---

## Files to Modify

### `pyproject.toml`
Add `"textual>=0.85"` to `dependencies` list (line 10-17).

### `tokenpal/config/schema.py`
Change `UIConfig.overlay` default from `"console"` to `"textual"` (line 36).

---

## What stays untouched

- `tokenpal/ui/console_overlay.py` — kept as fallback
- `tokenpal/ui/tk_overlay.py` — kept as fallback
- `tokenpal/ui/ascii_renderer.py` — `BuddyFrame` and `SpeechBubble` reused as-is
- `tokenpal/ui/registry.py` — autodiscovery already handles new modules
- `tokenpal/ui/base.py` — `AbstractOverlay` interface unchanged
- `tokenpal/app.py` — no changes needed; the overlay interface is stable
- `tokenpal/brain/` — no changes needed; communicates via callbacks

---

## Implementation Order

1. Add `textual>=0.85` to `pyproject.toml` dependencies
2. Create `tokenpal/ui/textual_overlay.tcss`
3. Create `tokenpal/ui/textual_overlay.py` (widgets -> app -> overlay, bottom-up)
4. Change default overlay in `schema.py` to `"textual"`
5. Smoke test: `pip install -e .` -> `tokenpal` — verify layout, typing animation, input, `/help`, auto-hide, Ctrl+C
6. Create `tests/test_ui/test_textual_overlay.py`
7. Run full suite: `pytest` (existing 62 tests + new UI tests)
8. Verify fallbacks: `overlay = "console"` and `overlay = "tkinter"` in config still work

---

## Verification

- **Visual:** Run `tokenpal`, observe header/buddy/input/status bar layout. Type a message, confirm speech bubble appears with typing animation. Wait for auto-hide. Try `/help`, `/status`, `/mood`.
- **Threading:** Confirm brain comments arrive and render correctly (brain thread -> `call_from_thread` -> UI)
- **Fallback:** Set `overlay = "console"` in `config.toml`, confirm old overlay still works
- **Tests:** `pytest tests/test_ui/` passes, `pytest` full suite still at 62+ tests
- **Lint/type:** `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean

---

## Risks

| Risk | Mitigation |
|------|-----------|
| `call_from_thread` before `app.run()` starts | Guard with `is_running` check; brain has 2s poll interval so app starts first |
| Unicode width for Cyrillic buddy art | Textual uses Rich internally with proper wcwidth — likely *better* than raw ANSI |
| Textual API changes | Pin `>=0.85`, only use core stable APIs (`Static`, `Input`, `App`) |
| Signal handling conflicts | Let Textual handle Ctrl+C natively via `BINDINGS`; `teardown()` calls `app.exit()` |
