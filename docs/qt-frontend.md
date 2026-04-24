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
  always-on-top QWidget, rigid-pendulum drag physics pivoted at the
  cursor's grab point (see below), edge-dock on release.
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

## Pendulum drag physics

The buddy is a rigid pendulum pivoted at whatever pixel on the art the
cursor grabbed. Grab the head and he dangles normally; grab a foot and
he dangles upside-down from it (COM is head-heavy, at `y = 30 %` of
the art so the inversion feels right); grab anywhere in between and
he swings around that point. Pure-Python simulator at
`tokenpal/ui/qt/physics.py:PendulumSimulator` (no Qt imports, fully
unit-testable). Semi-implicit Euler at 60 Hz.

State: `θ` (rad, signed so Qt's `painter.rotate(+deg)` renders CW in
screen, making feet sweep left), `θ_dot`, plus EMA-smoothed pivot
velocity / acceleration and a cursor-position history deque for
circular-motion detection.

ODE:

```
θ'' = −spin_fade · gravity · sin(θ)/L                       # restoring
      + spin_fade · drag · (vₓ·cosθ − vᵧ·sinθ)/(L·m)         # wind-drag
      + spin_fade · yank · (aₓ·cosθ − aᵧ·sinθ)/L             # yank impulse
      − (damping + drag/m) · damp_factor · θ'                # friction
      + coupling · ω_cursor_smoothed                         # circle drive
```

- **Gravity**: restores θ→0 at rest. Signed by Qt's rotation convention.
- **Wind-drag** (velocity-based forcing): constant-speed cursor drag
  produces a steady tilt; replaces the old acceleration-only model that
  let body drift back to vertical whenever cursor reached constant
  velocity.
- **Yank** (acceleration-based pseudo-force): impulsive kick on rapid
  cursor direction changes — the "whip" effect.
- **Damping**: `(damping + drag/m) · damp_factor · θ'`. `damp_factor`
  scales from 1 at rest down to `spin_damping_floor` at the angular
  speed cap so sustained twirls don't bleed energy.
- **Circular coupling**: `_cursor_angular_rate()` estimates the cursor's
  signed angular velocity around the running mean of its last 24
  samples (~0.4 s window) via a swept-area cross-product — exactly 0
  for linear / stationary motion, non-zero only for curved paths. The
  raw rate is EMA-smoothed so hand-jitter on a real circle doesn't
  sag coupling drive. Pure additive torque (NOT a PID on `ω_cursor −
  θ_dot`) so it only accelerates body in the cursor's direction and
  never brakes existing momentum.
- **`spin_fade`** (`1 − |θ_dot| / spin_lockout_rate`, clamped to
  `[0, 1]`): fades gravity + wind-drag + yank out as body spin grows.
  Keeps orbit clean (no sine-wave ω variation at the top), full force
  at rest so buddy settles cleanly. Damping stays active but its own
  `damp_factor` takes over at high ω.

Grabbing (`BuddyWindow._begin_drag` / `_reconfigure_pivot`) swaps
`_pivot_art` to the clicked art pixel while preserving visual pose:
`theta_visual = θ + angle_of_com_offset` is captured, pivot changes,
new θ is solved to match the same render rotation. Angles landing
within ~0.9° of the ±π inverted-equilibrium get nudged off so gravity
can take over (`_UNSTABLE_EPS`). On release, `_re_pivot_to_neutral()`
does the same swap back to the head — but with `pivot_world` set to
the head's current world position rather than the mouse release, so
the body's world geometry doesn't shift. Gravity then walks him
upright from his current orientation without a visual pop.

The simulator auto-sleeps when `|θ_dot| < settle_speed`, `|θ| <
settle_angle`, and the pivot is stationary for 15 consecutive ticks.
`BuddyWindow` stops its 60 Hz timer when the sim sleeps and wakes it
on drag / impulse / pivot change so a resting buddy burns no CPU.

**Debug HUD + log**: set `TOKENPAL_PHYSICS_DEBUG=1` before launch.
Adds a magenta crosshair at the pivot, text overlay with θ / ω /
cursor coords / L / state, and a 20 Hz trace to `/tmp/tokenpal-
physics.log` in space-delimited key=value format for analysis.

**Never block the physics timer thread**. The 16 ms QTimer slot does
one `sim.tick(dt)` + one `self.move(...)` + one `self.update()` and
returns. If you need to do more work, marshal it elsewhere.

## Edge-dock

`BuddyWindow._maybe_edge_dock` runs once on mouse release. If the
pivot ends up within 20 px of the screen edge under it (via
`QGuiApplication.screenAt(pivot)`, multi-monitor-safe), the pivot
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

## Font sizing and style

Two separate `FontConfig` blocks live under `[ui]` in config.toml:

```toml
[ui.chat_font]
family = ""          # empty → Qt default. Any OS-installed family name works.
size_pt = 13         # 0 → fallback (chat = 13 pt).
bold = false
italic = false
underline = false

[ui.bubble_font]
# Same fields; 0 → buddy overlay font size minus 1.
```

Schema: `FontConfig` in `tokenpal/config/schema.py`, registered in
`tokenpal/config/loader.py:_NESTED_FIELDS` so the TOML section
deserializes as a dataclass instance. Writer: `set_font(section, cfg)`
in `tokenpal/config/chatlog_writer.py` — mirrors the existing
`set_max_persisted` / `set_background_opacity` pattern.

Keyboard shortcuts (chat window only, dock + history):

- `Cmd +` / `Ctrl +` → bump chat font size by 1
- `Cmd -` / `Ctrl -` → shrink by 1
- `Cmd 0` / `Ctrl 0` → reset to 13 pt baseline

`QKeySequence.StandardKey.ZoomIn/ZoomOut` maps to Cmd on macOS and Ctrl
elsewhere natively — one binding, correct on every platform. The reset
uses the string `"Ctrl+0"`; Qt auto-remaps Ctrl to Cmd on macOS. Size
clamped to `[8, 48]` via `clamp_font_size` in `chatlog_writer.py`. The
speech bubble has no keyboard shortcut — it doesn't own focus.

Plumbing in `QtOverlay`: `_handle_chat_zoom(delta)` mutates the live
`_chat_font: FontConfig`, applies to both `ChatDock` and
`ChatHistoryWindow` via `apply_font(QFont)`, then fires
`set_chat_font_persist_callback` so `app.py` can write to disk.
`set_chat_font(cfg)` / `set_bubble_font(cfg)` + matching `apply_font_config`
on `SpeechBubble` handle dialog-driven changes. The bubble re-layouts
and invalidates its wrap cache on font change — `setFont(font)` alone
leaves stale `fontMetrics` cached values.

Options dialog (`qt/options_dialog.py::_build_font_group`): two
identical groups — family `QFontComboBox`, size `QSpinBox`, three
checkboxes (bold/italic/underline), and a live-preview `QLabel`.
`_FontGroupWidgets.baseline` snapshots widget state after construction
so the save path only emits a `FontConfig` result if the user actually
changed something (Qt's font combo auto-picks a family even when the
stored config has `family=""`, so comparing against the passed-in
`initial` would false-positive).

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

- `tests/test_qt_physics.py` — 23 tests for both simulators in
  isolation: legacy `DangleSimulator` + the active `PendulumSimulator`
  (rest + settle, cursor-direction tilt signs, angular-speed clamp,
  angular-impulse decay, long-integration stability, unstable-equilibrium
  handling).
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
