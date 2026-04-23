# Qt desktop frontend

TokenPal's default UI is a frameless, always-on-top desktop buddy plus
a tray / menu-bar icon plus a two-surface chat UI (input dock under the
buddy + standalone history window), all built on PySide6. The Textual terminal UI is still supported and becomes the
automatic fallback when Qt can't run. This doc is the map for anyone
touching code under `tokenpal/ui/qt/`.

Full design history is in `plans/shipped/new-ui-new-me.md`.

## Picking the frontend

Config: `[ui] overlay = "qt" | "textual" | "console"`. Default is `qt`.

The resolver in `tokenpal/ui/registry.py` (`resolve_overlay`) silently
falls back to `textual` when any of these is true:

- PySide6 wasn't installed (the `desktop` extra wasn't picked up).
- `TOKENPAL_HEADLESS=1` is set.
- On Linux only: neither `DISPLAY` nor `WAYLAND_DISPLAY` is present.

The specific reason is logged at INFO so `--verbose` runs can debug
config surprises. A typo like `overlay = "qtt"` still raises — only
the "Qt requested but unrunnable here" path is softened.

To install without the desktop extra:

```bash
bash scripts/install-macos.sh --headless      # same for install-linux.sh
powershell scripts/install-windows.ps1 -Headless
python3 setup_tokenpal.py --headless
```

## Architecture — the adapter seam

`tokenpal/ui/base.py:AbstractOverlay` is the contract the brain uses
to drive any frontend. It's an ABC with abstract lifecycle methods
(`setup`, `run_loop`, `teardown`, `schedule_callback`, `show_buddy`,
`show_speech`, `hide_speech`) plus no-op defaults for the capability
surface (`set_mood`, `load_voice_frames`, `set_environment_provider`,
`set_chat_persist_callback`, modal openers, etc.). A test at
`tests/test_ui_adapter_contract.py` asserts every registered overlay
satisfies the full surface — the brain never needs `hasattr` gates.

`QtOverlay` at `tokenpal/ui/qt/overlay.py` implements this on top of
PySide6. It owns:

- `BuddyWindow` (`qt/buddy_window.py`) — frameless / translucent /
  always-on-top QWidget, spring-pendulum drag physics (see below),
  edge-dock on release.
- `SpeechBubble` (`qt/speech_bubble.py`) — frameless bubble with
  per-character typing animation. Line wrap cached on text + width
  so the 30ms paint tick doesn't re-wrap every frame.
- `ChatDock` (`qt/chat_window.py`) — the always-visible input + status
  strip. Frameless, translucent, glass-pill `QLineEdit` (glass styling
  from `qt/_text_fx.py`), status label below. Only the line edit takes
  focus; the container is `WA_ShowWithoutActivating` and
  `FocusPolicy.NoFocus` so clicking the strip never steals focus from
  the user's active app. Reparents between two mount modes: floating
  top-level window under the buddy (follows via
  `position_changed` → `_reposition_dock`), or embedded into
  `ChatHistoryWindow`'s bottom slot when the buddy is hidden. Lines
  starting with `/` route to the command callback; anything else routes
  to the input callback.
- `ChatHistoryWindow` (`qt/chat_window.py`) — standalone frameless
  translucent chat log with `QTextBrowser` (clickable URLs), transparent
  viewport, glass-styled scrollbar, a glass drag handle at the top
  (frameless windows have no titlebar, so we paint our own grip), a
  dock slot below the log for the embedded `ChatDock`, and a "Hide"
  button at bottom-left. Starts hidden; tray menu / `toggle_chat_log` /
  F2 flip its visibility. Carries the persist/hydrate/link-click
  contract from the old monolithic `ChatWindow`.
- Text legibility: white text on translucent surfaces gets a symmetric
  `QGraphicsDropShadowEffect` glow (`blur=4, offset=(0,0), alpha=255`,
  equivalent to CSS `text-shadow: 0 0 4px black`). For `QTextBrowser`
  the effect is attached to the **viewport**, not the browser frame —
  `QAbstractScrollArea` paints text into the viewport, so an effect on
  the viewport casts only from glyph pixels.
- Dock placement is a state machine driven by `_update_dock_placement`
  / `_apply_dock_mode(mode)` where `mode ∈ {"floating", "embedded",
  "hidden"}`. Inputs are `_buddy_user_visible` + `_history_user_visible`
  tracked as explicit user-intent state (separate from Qt's
  `isVisible()`, which lies on macOS due to the NSWindow auto-hide on
  app deactivate). `_toggle_buddy` and `_do_toggle_chat` flip their own
  flag and call `_update_dock_placement()` — neither window's toggle
  touches the other's state.
- `BuddyTrayIcon` (`qt/tray.py`) — `QSystemTrayIcon`. Clicking the
  icon only pops the context menu (Show/Hide buddy · Show/Hide chat log
  · Options… · Quit); no direct single-click or double-click toggle, to
  avoid the macOS menu-bar-click-accidentally-hides-buddy footgun.
- Modals: `ConfirmDialog`, `SelectionDialog` (`qt/modals.py`),
  `OptionsDialog` (`qt/options_dialog.py`). `_OneShotCallback` mixin
  guarantees the caller's `on_result` fires exactly once across
  every accept path (Save, Clear, Apply, Launcher, Cancel, Esc).
  Cloud / voice modals inherit `AbstractOverlay`'s False default
  and the app falls back to the slash-command text UI.

## Thread model

The brain runs on a daemon thread and calls adapter methods directly.
Qt widgets must be touched only on the main thread. Every brain-thread
method wraps its work in a 0-arg callable and emits it on
`_UIBridge.dispatch` (`qt/overlay.py:_UIBridge`) — a `QObject` with a
`Signal(object)` connected with `Qt.ConnectionType.QueuedConnection`.
The queued connection auto-marshals every emission onto the main
thread, and Qt delivers them FIFO.

Pre-setup calls (brain firing adapter methods before `setup()` has
built the widgets) are buffered in `self._pending_post` and replayed
on mount via `_bridge.dispatch.emit(fn)` so nothing silently drops.

### Don't do this

- Never call widget methods from the brain thread directly. Always
  wrap in a lambda and route through `_post(lambda: ...)` or
  `schedule_callback(fn, delay_ms)`.
- Never listen to `QDialog.finished` in the same class that listens
  to `accepted` / `rejected` — `finished` fires on every accept/reject
  and double-invokes your callback. `_OneShotCallback` exists only
  as belt-and-suspenders in case that pattern leaks back.

## Dangle physics

The buddy hangs from an anchor via a damped spring-pendulum.
Pure-Python simulator at `tokenpal/ui/qt/physics.py:DangleSimulator`
(no Qt imports, fully unit-testable).

State: `(pos, vel)`, anchor `a`, semi-implicit Euler at 60 Hz.

- `F_spring = -k * (pos - a)` — Hooke.
- `F_gravity = (0, +g)` — gravitational droop, so rest sits at
  `anchor + (0, g*m/k)` below the anchor (~7 px at defaults).
- `F_damping = -c * vel` — ζ ≈ 0.45, settles from hard displacement
  within ~1.5 s.

Drag moves the **anchor**, not the body — the spring pulls the body
along, which is what makes the motion feel connected by string.
Release carries the last ~80 ms of cursor velocity into the body as
an impulse so flicking sends him swinging. The simulator auto-sleeps
when `|vel| < 1 px/s` and the body is within 0.5 px of rest for 10
ticks; `BuddyWindow` stops its 60 Hz timer when the sim sleeps and
wakes it on drag / impulse / anchor change so a resting buddy burns
no CPU.

**Never block the physics timer thread**. The 16 ms QTimer slot does
one `sim.tick(dt)` + one `self.move(...)` and returns. If you need
to do more work, marshal it elsewhere.

## Edge-dock

`BuddyWindow._maybe_edge_dock` runs once on mouse release. If the
anchor ends up within 20 px of the screen edge under it (via
`QGuiApplication.screenAt(anchor)`, multi-monitor-safe), the anchor
snaps to the edge. Tuning lives in `_EDGE_DOCK_THRESHOLD`.

## Platform notes

`tokenpal/ui/qt/platform.py` collects OS-specific polish in one place
so the `if sys.platform ==` branches are easy to audit.

- **macOS**: `apply_macos_accessory_mode()` calls
  `NSApplication.setActivationPolicy_(NSApplicationActivationPolicyAccessory)`
  so the buddy lives only in the menu bar (no Dock icon). **Must run
  AFTER `QApplication` is constructed** — the `NSApplication` it pokes
  is the one Qt built. `QtOverlay.setup()` orders this correctly with a
  warning comment in both call sites.
- **Linux / Wayland**: `warn_wayland_limitations()` logs one INFO line
  that `WindowStaysOnTopHint` is compositor-dependent (works on KDE and
  sway, inconsistent on GNOME). Purely informational — the buddy still
  works, it just might fall behind other windows.
- **Windows**: standard tray integration; left-click toggles the buddy
  (Linux too, via `QSystemTrayIcon.activated`).

Hi-DPI scaling: `ensure_qapplication()` in `qt/__init__.py` sets
`Qt.HighDpiScaleFactorRoundingPolicy.PassThrough` before the
`QApplication` is constructed so the buddy renders crisply on Retina
and 4K without Qt snapping fractional scale factors to integers.

## Mood and voice frame swaps (Phase 4 TODO)

`QtOverlay.set_mood(mood)` currently only stores `_current_mood`.
`load_voice_frames` uses it to pick the initial frame set at voice-load
time. A mood change after frames are already loaded does not yet
re-render — that's a known gap marked with `TODO(phase4)` at
`qt/overlay.py:set_mood`. The Textual overlay handles this via its
mood-frame indexing; the Qt port will grow the same behavior once the
voice-frame test matrix covers it end-to-end.

## Adding a new overlay

1. Subclass `AbstractOverlay`, set `overlay_name` and `platforms`.
2. Decorate with `@register_overlay`.
3. Implement the abstract methods. Optional capability methods can stay
   as-is and inherit no-op defaults.
4. Drop the module under `tokenpal/ui/` or a subpackage —
   `discover_overlays()` walks everything under `tokenpal.ui`.
5. Add your class to the contract test's expected list (auto if you use
   `list_overlays()`; manual otherwise).

## Tests

- `tests/test_qt_physics.py` — 12 tests for the simulator in isolation.
- `tests/test_qt_shell.py` — shell boots, frameless flags set, tray
  menu has Toggle + Quit.
- `tests/test_qt_overlay.py` — every brain-invoked method end-to-end,
  pre-setup buffering, `_on_user_submit` dispatch, history hidden by
  default, `toggle_chat_log` flips history visibility, status prefix
  composition order.
- `tests/test_qt_dock_follow.py` — dock placement state machine
  (floating / embedded / hidden) and chat dock moves with the buddy via
  `position_changed` and sits below the buddy's bottom edge.
- `tests/test_qt_edge_dock.py` — edge snaps on all 4 sides + mid-screen
  no-op.
- `tests/test_qt_slash_dispatch.py` — `/` routes to command_callback.
- `tests/test_qt_modals.py` — ConfirmDialog / SelectionDialog contracts.
- `tests/test_qt_options_dialog.py` — options modal parity with Textual.
- `tests/test_qt_platform.py` — macOS accessory mode, Wayland warn,
  tray activation reasons.
- `tests/test_overlay_resolve_fallback.py` — silent fallback triggers.
- `tests/test_ui_adapter_contract.py` — every registered overlay
  satisfies the full ABC surface.

All tests skip automatically via `pytest.importorskip("PySide6")` when
the desktop extra isn't installed.
