# new-ui-new-me — free the buddy from the terminal

## Goal
Add a second frontend that lives as a frameless, always-on-top desktop buddy plus tray/menu-bar icon, sharing the existing Brain/Senses/Actions backend with the current Textual UI. Keep the terminal UI fully working — it becomes "headless dev mode" while the new shell is the default user experience.

## Non-goals
- Rewriting Brain, Senses, Actions, or the LLM layer. Backend is untouched.
- Ripping out Textual. Terminal UI stays as a supported runtime (opt-in via flag/config).
- Native per-OS apps (Swift / C# / GTK). One Python toolkit, cross-platform.
- Electron / Tauri / webview. Heavy, non-Python, auth headaches with our existing async threading.
- Full rigid-body / collision physics. The "dangle" is a single pendulum-plus-spring toy, not a physics engine.
- Auto-start at login, notifications beyond the existing bubble. Later.
- Touching the voice training / fine-tuning / research pipelines.
- Click-reaction gestures (poke / shake) on the Qt buddy. The physics flick itself is v1's feedback; hand-waving the cursor at him is parking-lot.

## Proposed toolkit
**PySide6 (Qt 6)** as the primary. Rationale:
- `QSystemTrayIcon` handles Windows system tray, macOS menu bar, and Linux StatusNotifier in one API, with `setContextMenu(QMenu)` for the right-click.
- Frameless transparent windows (`Qt.FramelessWindowHint | Qt.WA_TranslucentBackground | Qt.WindowStaysOnTopHint`) are first-class on all three OSes.
- ASCII art renders cleanly in a `QLabel` with an HTML subset (our Rich markup maps to `<span style="color:#hex">` snippets) or in a custom `QWidget.paintEvent` if we want per-cell control for particles.
- Drag is a 6-line `mousePressEvent` + `mouseMoveEvent` override.
- LGPL license is fine for our private repo; pip-installable wheels exist for macOS/Win/Linux including ARM.

PyQt6 is the backup if PySide6 has a packaging issue on one of our target rigs.

## Architecture — the adapter seam

Today Brain → Textual coupling happens in two places:
1. `tokenpal/ui/textual_overlay.py` — subclasses `App`, defines `Message` subclasses, receives events via `post_message()` from the brain thread.
2. `tokenpal/app.py` — wires overlay ↔ orchestrator, owns slash command dispatch, hydrates chat log, etc.

Introduce a thin `UIAdapter` protocol (or ABC) — the full surface matches the existing Textual overlay's Message types + callbacks so nothing silently regresses:

**Brain → UI (speech / status / chat / frames):**
- `show_bubble(text, voice, typing=True)` (maps to `ShowSpeech`)
- `hide_bubble()` (`HideSpeech`)
- `update_status(mood, server, model, voice, app, weather, music, spoke_ago)` (`UpdateStatus`)
- `append_chat(ts, author, text, link_urls=None, markup=False)` — covers both `LogBuddyMessage` (markup) and `LogUserMessage` (plain)
- `clear_chat()` (`ClearLog`)
- `load_chat_history(rows)` — startup hydration
- `load_voice_frames(idle, idle_alt, talking, mood_variants)` (`LoadVoiceFrames`)
- `clear_voice_frames()` (`ClearVoiceFrames`)
- `set_mood(name)` (`SetMood`)
- `update_environment_state(snapshot)` (`UpdateEnvironmentState`)

**Brain → UI (modals):**
- `open_selection_modal(groups, on_result)` (`OpenSelectionModal`)
- `open_confirm_modal(prompt, on_result)` (`OpenConfirmModal`)
- `open_options_modal(state, on_result)` (`OpenOptionsModal`)
- `open_voice_modal(state, on_result)` (`OpenVoiceModal`)
- `open_cloud_modal(state, on_result)` (`OpenCloudModal`)

**Brain → UI (lifecycle / deferred work):**
- `start()`, `stop()`, `request_exit()` (`RequestExit`)
- `run_callback(fn, delay_ms=0)` (`RunCallback`; Qt impl uses `QTimer.singleShot`)
- `set_environment_provider(fn)` — 10 Hz pull for particles/physics

**UI → Brain callbacks (wired by app.py during boot):**
- `set_input_callback(fn)` — user text → `brain.submit_user_input()`
- `set_command_callback(fn)` — slash commands → `app.py` dispatch
- `set_buddy_reaction_callback(fn)` — poke/shake → `brain.on_buddy_poked/shaken` (Textual-only in v1; the Qt adapter implements the hook as a no-op so `hasattr` checks still pass)
- `set_chat_persist_callback(fn)` — `(speaker, text, url)` → `MemoryStore.append_chat_log`
- `set_chat_clear_callback(fn)` — triggers persist wipe

Both `TextualOverlay` and the new `QtOverlay` implement this. `app.py` chooses via the **existing** `[ui] overlay` config key — values extended from `"textual" | "console"` to `"qt" | "textual" | "console"`. **Qt is the default** on macOS/Windows/Linux desktop. Textual takes over silently when: `TOKENPAL_HEADLESS=1`, no display is detected, PySide6 isn't installed, or `QApplication` construction raises. No error screen — just log at INFO and boot Textual.

**Shared vs frontend-specific config:** the existing flat `UIConfig` fields (`buddy_name`, `font_family`, `font_size`, `position`, `chat_log_width`) remain **shared** cross-frontend settings that the Qt adapter must honor. Qt-specific knobs live in a new nested `[ui.qt]` sub-section (`anchor_xy`, `physics.*`, `show_string`, `always_on_top`, `start_hidden`, `docked_edge`).

**In-app frontend switch**: the Options modal exposes a "Frontend" selector. Picking a different frontend writes `[ui] overlay` to `config.toml` and the app offers to restart (confirm dialog; `tokenpal --restart` re-execs via `os.execv(sys.executable, [sys.executable, "-m", "tokenpal", *sys.argv[1:]])`). No hot-swap — the event loops don't coexist.

This seam also lets the brain stop importing Textual types directly — reduces blast radius for future frontends (web, mobile, etc.) without committing to them.

## Qt frontend layout

Three top-level windows, all driven from a single `QApplication`:

1. **Buddy window** — frameless, transparent, always-on-top. Hosts the voice ASCII art + speech bubble + particle overlay. Drag to move (the physics flick is the only "reaction" in v1; no poke/shake gestures). Right-click = same menu as tray.
2. **Chat window** — shown/hidden via tray menu and a click on the buddy. Scrollable chat log with the same Rich markup → HTML translation used by the buddy. Input box at the bottom. Resizable, remembered position.
3. **Tray/menu-bar icon** — `QSystemTrayIcon` with a static icon (we reuse one of the voice ASCII frames rasterized, or a dedicated 32×32 PNG per voice). Right-click menu: Show/Hide buddy · Show chat · Voice ▸ · Mood ▸ · Options · Pause commentary · Quit.

## "Dangle-able" v1 — springy physics spec

The buddy hangs from an **anchor point** by an invisible string/spring. This is core to v1.

**Anchor lifecycle**
- Default anchor: last committed resting spot (persisted to `[ui.qt] anchor_xy` on quit).
- While dragged: anchor follows the cursor (buddy trails behind, tugged by the spring).
- On release: anchor stays where the cursor let go; buddy swings into it and settles.
- Edge-dock: if released within 20 px of a screen edge, anchor snaps to the edge; buddy dangles from the edge.

**Simulation model** — 2D damped spring-pendulum, state `(pos, vel)`, anchor `a`:
- `F_spring = -k * (pos - a)`  (Hooke, `k` ≈ 180 N/m-equivalent tuned in screen pixels)
- `F_gravity = (0, +g)`  (`g` ≈ 1200 px/s² — feels weighty but not laggy)
- `F_damping = -c * vel`  (`c` ≈ 6 — critical-ish damping so it settles in ~1.5 s)
- Integrate with semi-implicit Euler at 60 Hz: `vel += (F/m) * dt; pos += vel * dt` (`m` = 1).
- Clamp `|vel|` to a sane max so a violent flick can't launch the buddy off-screen.
- Sleep threshold: when `|vel| < 1 px/s` and `|pos - rest| < 0.5 px` for 10 consecutive ticks, suspend the physics timer. Resume on drag or anchor change.

**Drag feel**
- `mousePressEvent` captures the grab offset and starts the physics timer if sleeping.
- `mouseMoveEvent` moves the **anchor**, not the buddy. The spring pulls the body toward the new anchor — that's what makes it feel connected by string.
- Flinging: on release, carry the last ~3 frames of cursor velocity into the buddy as an impulse so a quick whip sends him swinging.

**Visual sugar**
- Thin Bézier from anchor → buddy's top-attach glyph, drawn in `paintEvent`. Optional; hide behind `[ui.qt] show_string = true`.
- Tail / hair / antenna glyph in `ascii_props.py` bobs based on `vel.y` so you can feel the swing.

**Tunables** in `[ui.qt] physics`: `spring_k`, `gravity`, `damping`, `mass`, `max_speed`, `settle_threshold`. Defaults ship in `config.default.toml`. Hot-reload not required.

**Sensitive-app freeze**: same rule as the particle overlay — `EnvironmentSnapshot.sensitive_suppressed` halts the physics timer and parks the buddy at its anchor so no swinging while the user is in a banking app.

## Files to touch

New:
- `tokenpal/ui/qt/__init__.py`
- `tokenpal/ui/qt/adapter.py` — `UIAdapter` protocol / ABC (lives under `qt/` only if that's where it's born; may promote to `tokenpal/ui/adapter.py` if the textual side reshapes to implement it too — decide in research pass)
- `tokenpal/ui/qt/app.py` — QApplication boot, window wiring, signal routing
- `tokenpal/ui/qt/buddy_window.py` — frameless buddy, drag, edge-dock
- `tokenpal/ui/qt/chat_window.py` — chat log + input
- `tokenpal/ui/qt/tray.py` — QSystemTrayIcon + context menu
- `tokenpal/ui/qt/bubble.py` — speech bubble widget (typing animation via QTimer)
- `tokenpal/ui/qt/ascii_render.py` — Rich markup → Qt HTML (or QPainter) translator
- `tokenpal/ui/qt/particles.py` — port of `buddy_environment.py` rendering to QWidget (logic layer stays in `buddy_environment.py`)
- `tokenpal/ui/qt/physics.py` — pure-Python spring-pendulum integrator (no Qt imports, fully unit-testable)
- `tests/test_qt_physics.py` — step the integrator, assert settle time, sleep threshold, impulse response
- `tokenpal/ui/qt/modals/options.py` + sibling modal ports
- `tests/test_qt_adapter.py` — mock `UIAdapter` calls, verify brain → adapter contract (no QApp needed)

Edited:
- `tokenpal/app.py` — pick frontend from config, construct adapter, wire lifecycle
- `tokenpal/ui/textual_overlay.py` — refactor to implement `UIAdapter` without behavior change
- `tokenpal/config/schema.py` — extend existing `[ui] overlay` enum to include `"qt"`; add nested `QtUIConfig` dataclass (`anchor_xy`, `physics.*`, `show_string`, `always_on_top`, `start_hidden`, `docked_edge`) under `UIConfig.qt`
- `config.default.toml` — defaults
- `pyproject.toml` — `PySide6` as an extra (`tokenpal[desktop]`), not core, so headless installs stay lean
- Platform installers — add a new `--headless` flag to `scripts/install-macos.sh` (~line 204 extras assembly), `scripts/install-windows.ps1` (~line 212), `scripts/install-linux.sh` (~line 213). Default path includes `tokenpal[desktop]`; `--headless` omits it and leaves only Textual. `setup_tokenpal.py` grows the same flag
- `run.sh` / `run.ps1` — no changes expected; verify during integration test
- `docs/qt-frontend.md` — new architecture doc, cross-linked from CLAUDE.md
- `CLAUDE.md` — UI section addition (which frontend, how to switch, adapter seam)

## Failure modes to anticipate

- **Threading boundary.** Qt requires UI calls on the main thread. Today the brain runs on a daemon thread and posts Textual Messages. Need Qt signals (`Signal.emit()` is thread-safe, queues onto the UI thread) — do not call widget methods directly from the brain thread. Mistakes here look like silent no-ops or crashes on Windows.
- **`QApplication` vs Textual event loop.** Can't run both simultaneously in one process. Frontend selection must be exclusive at startup. `[ui] frontend = "qt"` means Textual never starts.
- **macOS menu-bar quirks.** `QSystemTrayIcon` on macOS puts us in the status bar, NOT the Dock. We also want no Dock icon — set `LSUIElement` / `NSApplication.setActivationPolicy(.accessory)` via `QApplication.setQuitOnLastWindowClosed(False)` plus an Info.plist bit when packaging. Iterating this on the actual mac is mandatory (memory: author-on-target-host).
- **Frameless + always-on-top on Linux/Wayland.** `Qt.WindowStaysOnTopHint` is a hint; Wayland compositors may ignore it. Test on GNOME and KDE. Fall back gracefully.
- **Input focus stealing.** Always-on-top + text input = we can accidentally grab focus from the user's editor. Buddy window must be click-through for most of its surface; only the input widget in the chat window takes focus.
- **Rich-markup → Qt HTML parity.** Our voice frames use Rich color names we already remap (see `ascii_renderer._fix_markup`). Qt's QLabel HTML is stricter than Rich's markup. Reuse the existing `_fix_markup` pipeline and then emit `<span style>` rather than duplicating the color table.
- **Chat log backpressure.** Textual re-renders via Rich; Qt's `QTextEdit.append()` is cheap but unbounded document growth in a long session will lag. Port the 500-line cap + persistence logic from the overlay verbatim.
- **Hi-DPI & scaling.** Windows/macOS hi-DPI mostly just works with `QApplication.setHighDpiScaleFactorRoundingPolicy`. Linux mixed-DPI is historically rough.
- **Packaging.** PyInstaller/py2app bundling of PySide6 adds ~60-80 MB. Keep `tokenpal[desktop]` optional to avoid forcing that onto `/validate`-only installs.
- **Privacy.** Sensitive-app suppression already freezes the buddy's environment; confirm the new Qt renderer respects `sensitive_suppressed` in `EnvironmentSnapshot` same as Textual does.
- **Modal stacking.** Textual has `_modal_already_active` guard (textual_overlay.py:1529). Qt's `QDialog.exec()` stacks naturally but we still want the same one-at-a-time UX — port the guard pattern.
- **`hasattr(overlay, ...)` introspection.** `app.py` (lines ~263 and ~270) uses `hasattr` to feature-gate overlay capabilities. If the Qt adapter only *partially* implements `UIAdapter`, those branches silently no-op and bugs look like "feature just missing." Guard rail: `UIAdapter` is an ABC with `@abstractmethod` on every member, and tests assert both adapters instantiate.
- **ARM64 macOS wheel.** PySide6 ships `cp312-macosx_11_0_arm64` wheels, but until we install on the M-series rig in Phase 0 it's untested for us. If the wheel is broken or slow, PyQt6 is the fallback (memory: author-on-target-host).

## Done criteria
- `tokenpal` launches with `[ui] overlay = "qt"` and shows a frameless always-on-top buddy, tray/menu-bar icon, and a hideable chat window on macOS, Windows, and Linux.
- Tray/menu-bar right-click shows Show/Hide buddy · Show chat · Voice ▸ · Mood ▸ · Options · Pause · Quit, and each item works.
- Buddy dangles from an anchor with springy physics: dragging moves the anchor and the body trails behind on a damped spring; release leaves him swinging, settling within ~1.5 s. Flicking fast sends him swinging harder.
- Options modal has a "Frontend: Qt / Textual" picker that persists to `[ui] overlay` and restarts cleanly.
- Buddy is draggable, edge-docks to screen edges, survives multi-monitor setups, and stays on top across space switches on macOS.
- Every feature available in the Textual UI (speech bubble typing animation, chat log + persistence, status line, particle overlay, options/senses/tools/voice/cloud modals, slash commands, `/ask` click-through URLs, voice frame mood swaps) has a working equivalent in the Qt UI.
- Every `hasattr(overlay, ...)` branch in `app.py` is exercised on the Qt adapter — no silent no-ops. A unit test instantiates both adapters and asserts no abstract methods remain.
- Headless fallback is silent: if PySide6 is missing, `DISPLAY` is unset, or `QApplication()` raises, the app boots Textual and logs an INFO line naming the reason. Zero user-facing error.
- `[ui] overlay = "textual"` still launches the existing terminal UI with zero behavior regressions.
- `pytest` green, `ruff check` clean, `mypy --ignore-missing-imports` clean on both `tokenpal/ui/qt/` and the refactored Textual side.
- `docs/qt-frontend.md` exists and CLAUDE.md is updated.
- Installed via `tokenpal[desktop]` extra on at least one clean machine per OS (Apple Silicon mac counts for the ARM64 wheel check).

## Phasing (proposed — confirm before coding)
0. **Wheel sanity**: `pip install PySide6` into a throwaway venv on the M-series mac, confirm the ARM64 wheel imports and can pop a `QApplication`. If broken, fall back to PyQt6 *before* any file lands. No commit.
1. **Adapter seam**: define `UIAdapter` ABC covering the full surface above, refactor Textual overlay to implement it, prove brain → adapter contract with a mock. No Qt yet. Commit.
2. **Qt skeleton + physics**: QApplication + frameless buddy showing a static ASCII frame + tray icon with a stub menu + spring-pendulum integrator unit-tested in isolation. No brain wiring. Commit.
3. **Brain wiring**: Qt adapter forwards bubble / status / chat / frames / environment signals. Textual still default on disk. Commit.
4. **Parity pass**: all five modals, slash dispatch, chat persistence + hydration, hi-dpi, edge-dock, silent-headless fallback. Commit.
5. **Platform hardening**: macOS `LSUIElement`, Linux Wayland check, Windows tray polish, packaging extras, `--headless` installer flag. Commit.
6. **Docs + flip default**: CLAUDE.md, qt-frontend.md, installers default to desktop extra, `[ui] overlay` default flipped to `"qt"`. Commit.

## Parking lot
- `app.py:408` voice-regenerate has inverted confirm semantics: `if not overlay.open_confirm_modal(...): _do_regen()`. On overlays that lack a modal (returns False) this regenerates **without** asking, burning ~60s of LLM work on an unintended trigger. Pre-dates Phase 4; Qt's text-fallback path for the richer modals makes it visible. File as its own small PR.
- Full Qt ports of cloud and voice modals. Currently they inherit AbstractOverlay's False default and callers fall back to the slash-command text UI, which works. Visual parity is a later pass. (Options modal has been pulled into v1 — see qt/options_dialog.py.)
- Async `/v1/models` probing for non-active servers in the Qt options dialog. Textual probes in the background on server-row click; Qt version shows "(switch to see models)" for non-displayed servers until the user saves. Add a QThread + signal probe pattern so the Qt modal matches Textual's live-probe behavior.
