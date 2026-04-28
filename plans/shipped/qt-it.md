# qt-it

## Context

Buddy still jitters intermittently on a 4K @ 144Hz Windows display, AND the user sees a faint "shadow rotating with him too fast to make out" during motion. Master-sprite + θ/position lerp + sub-pixel residual + velocity-aware AABB slack are all in place and verified — these aren't the issue. Something else in the Qt → DWM → display pipeline is presenting a *different* state than what the lerp computed, OR a second window is rendering at a different timestamp than the buddy.

Frequencies in play:
- Physics: 240 Hz (4.17 ms / step)
- Pump (`_on_tick`): ~166 Hz (6 ms PreciseTimer)
- Qt paint coalescing: 1 paintEvent per Qt event-loop iteration
- DWM compositor + display refresh: 144 Hz (~6.94 ms / vsync)
- 4K @ DPR 1.5–2.0 → master pixmap ≈23 MB, paint fillrate non-trivial

These don't divide evenly. There's a beat between the 166 Hz pump and 144 Hz refresh that lerp alone can't fully hide if other parts of the stack are sampling state at inconsistent timestamps.

## Goal

End-to-end audit of the Qt + DWM + Windows-LAYERED-window pipeline to find every place where state is sampled at one timestamp but consumed at another. Identify what the "rotating shadow" actually is. Smooth motion at every zoom on a 4K @ 144Hz display, no visible echoes.

**Second deliverable, equal weight**: a research-backed recommendation on whether the current `QWidget + WA_TranslucentBackground + QPainter` stack has a structural smoothness ceiling on modern displays (4K @ 144Hz+) and whether a migration to a different presentation layer is the prudent move. If yes, name the target stack, prove it preserves every existing feature, and lay out the migration cost. If no, show the math/evidence that says "we can fix this in place."

## Non-goals

- Touching `tokenpal/ui/qt/physics.py`. The simulator is not the suspect — this is presentation-side, regardless of backend.
- Reworking the Fix-Your-Timestep accumulator, θ/position lerp, AABB slack, sub-pixel residual. Those landed and are load-bearing; whatever backend we pick still needs them.
- Dropping any current feature in service of a migration. Per-pixel transparency, click-through-when-not-painted, always-on-top, multi-monitor, drag/fling/zoom physics, speech-bubble follower, weather/sky compositing, chrome/resize-grip overlay, Textual fallback path, tray icon, voice modal — all stay. A backend that can't preserve all of these is disqualified.
- Cross-platform regression. macOS (M-series), Linux (Wayland + X11), Windows (DWM) must all work. A Windows-only fix is OK as a tactical patch but a backend migration must be cross-platform from day one.
- Rewriting the brain / actions / senses / LLM stack. UI-layer scope only.
- Pump-rate changes from 6 ms (Qt's documented Windows floor) — unless the new backend's vsync driver replaces the pump entirely.

## Files to touch

- `tokenpal/ui/qt/buddy_window.py` — paint event timing, move/update/setMask ordering, possibly `repaint()` instead of `update()` for sync paint, possibly defer `move()` into paintEvent.
- `tokenpal/ui/qt/speech_bubble.py` — top suspect for the "shadow." Reads `head_world_position()` + `body_angle()` and paints rotated text. Its paintEvent runs on its own timer/signal.
- `tokenpal/ui/qt/weather.py` — also reads buddy state for sky/particle compositing; same risk.
- `tokenpal/ui/qt/_chrome.py` — chrome / resize-grip overlay; reads buddy bounds.
- `tokenpal/ui/qt/overlay.py` — orchestrates the buddy + followers. Where `position_changed.emit()` connects to follower update paths. Sync coupling lives here.
- `tokenpal/ui/qt/dock_mock.py` — dock follower's mock-form rendering during motion.
- POSSIBLY a new `tokenpal/ui/qt/_paint_clock.py` — single source of truth for "current paint timestamp" so all windows lerp to the SAME `now`, not their own private wall-clock samples.
- POSSIBLY (if research recommends migration) a new backend tree under `tokenpal/ui/<backend>/` — e.g. `tokenpal/ui/quick/` for QtQuick, `tokenpal/ui/gl/` for QOpenGLWidget, `tokenpal/ui/web/` for a web-stack experiment. Migration would be staged: new backend behind a feature flag, validate on all three platforms, swap default, retire old. The existing `BackendRegistry` pattern (used for inference engines) is the model.

## Approach (investigation-first; fixes flow from findings)

### Phase 0 — Instrument the unknown

Before touching any sync code, prove the hypothesis. Two diagnostic env-var-gated paths:

1. **`TOKENPAL_PAINT_TRACE=1`**: each window logs `(window_name, paint_t_monotonic, theta_used, pos_used)` per paintEvent. Capture 5 seconds of buddy-in-motion logs. If buddy paints at θ=0.40 rad and bubble paints 4 ms later at θ=0.43 rad, the shadow is just the bubble lagging — root cause confirmed in 30 seconds.
2. **`TOKENPAL_PAINT_HOLD=1`**: temporarily replace the bubble's paint with a no-op (transparent fill). If the shadow disappears, it IS the bubble. Repeat for weather, chrome, dock. Process of elimination.

### Phase 1 — Single-clock paint coordination

If phase 0 confirms followers are out of sync, route ALL window lerps through one shared `_paint_clock`:
- BuddyWindow exposes `current_paint_state(now: float) -> (theta, com_x, com_y)` that does the lerp deterministically given any timestamp.
- Followers read this with the SAME `now` value the buddy uses. No private `time.monotonic()` calls in follower paint paths.
- All windows' paintEvents ideally see the same DWM frame; if they can't (Qt schedules each window's paint independently), at least they project to the same target timestamp.

### Phase 2 — Move-then-update mismatch

`_move_to_com` is called from `_on_tick` and synchronously moves the WM window, then `update()` schedules a paint LATER. Between move and paint, the OLD backbuffer (stale θ) is shown at the NEW screen position — a 1-frame mismatch every tick.

Options:
- **A**: Defer `widget.move()` into paintEvent. Compute target position from lerped state, move the widget right before drawing into the new position. Side-effect: paintEvent now mutates layout, which Qt frowns on. Mitigation: Qt allows `move()` from paint as long as we don't trigger a new paintEvent recursively.
- **B**: Move + paint atomically by switching from `update()` (async) to `repaint()` (sync) after move. Cost: bypasses Qt's coalescing — every tick triggers a real paint, even at 166 Hz. May actually IMPROVE smoothness if it locks paint to physics rather than letting DWM choose.
- **C**: Don't move the widget every tick — keep the widget large and stationary, render the buddy at any sub-position via the world transform. Eliminates the move/paint race entirely. Cost: widget covers a much bigger screen rect → bigger click mask → more click-through grief.

Decide between A/B/C based on phase 0 telemetry — which actually shows the worst staleness?

### Phase 3 — DWM presentation timing

QWidget paints land in a DWM-managed backbuffer. Windows DWM queues 1–2 frames; `WA_TranslucentBackground` routes through `UpdateLayeredWindow` which has different presentation semantics from normal windows. Investigate:
- Whether `Qt.WA_NoSystemBackground` + manual full-clear in paintEvent helps avoid stale-pixel artifacts.
- Whether `setAttribute(Qt.WA_OpaquePaintEvent)` (no — incompatible with translucent).
- Whether forcing `WindowStaysOnTopHint` interacts badly with DWM frame queuing.
- Whether there's a Qt API to get DWM vsync timestamps so we can target paint timing.
- Confirm: does Qt issue `DwmFlush()` between paints? If not, manually calling it might serialize paint with vsync.

### Phase 4 — Resize churn

`_recompute_geometry` may resize the widget every tick (especially with velocity-aware `pos_slack` which changes every tick during accel). At 166 Hz that's up to 166 WM resize events per second. WM resize triggers Qt-internal layout pass + WM round-trip. Audit:
- Is the no-op guard (`if size != current size: resize(...)`) actually firing most ticks, or is `pos_slack` jittering by 1 px every tick and forcing a real resize?
- If real-resize churn is high, snap `pos_slack` to a step function (e.g. round up to nearest 4 px) so the widget only resizes when slack crosses a boundary.

### Phase 5 — Pump-vs-vsync beat

166 Hz pump vs 144 Hz refresh = beat at 22 Hz, which is in the human flicker-perception range. Even with perfect lerp, alpha is sampled at varying values across frames, which can read as motion irregularity. Investigate:
- Drop pump to 144 Hz to match the display? Cost: physics steps may queue up (each tick drains 4 physics steps at this rate). Lerp absorbs it but extrapolation past α=1 grows.
- Or: drive pump from `QScreen::vsync` signal if available, syncing pump to display refresh. Confirm Qt 6 / PySide 6 exposes this.

### Phase 6 — DPR / fillrate reality check

At 4K with display scaling 150%, `devicePixelRatioF()` may return 1.5. Master pixmap ~23 MB. drawPixmap with rotation samples every destination pixel. If paint takes >7 ms (one vsync at 144 Hz), frames drop. Measure paint duration via existing `TOKENPAL_TICK_PROFILE=1` (interval vs body times) — if body p99 > 7 ms, fillrate is the bottleneck and CPU rasterization is structurally too slow → phase 7 becomes the answer regardless of phase 0–5 findings.

### Phase 7 — Backend evaluation: stay vs migrate

Equal-weight deliverable to the bug fix. The research dispatch (step 7.5) treats this as a first-class question: is `QWidget + WA_TranslucentBackground + QPainter` structurally capable of smooth animation on 4K @ 144Hz+, or is there a ceiling we keep hitting?

Candidates to evaluate (each must answer the same matrix below):

1. **Stay**: `QWidget + WA_TranslucentBackground + QPainter` (current). Status quo with phase 1–6 fixes applied. Baseline.
2. **QtQuick / QML + scene graph**: GPU-accelerated, retained-mode, vsync-driven by Qt's render thread. Supports `Qt.WA_TranslucentBackground` equivalent via `setColor(Qt.transparent)` on `QQuickWindow`. Native cross-platform.
3. **QOpenGLWidget inside a translucent QWidget**: keeps the WA_TranslucentBackground host, swaps QPainter for GL inside. Risky — translucent QOpenGLWidget historically has Windows compositor bugs.
4. **Native + Qt hybrid**: keep Qt for menus/dialogs/tray; render the buddy + followers in a single native always-on-top click-through window per platform (CAMetalLayer on macOS, DirectComposition on Windows, wlr-layer-shell on Wayland, override-redirect X11). Highest control, highest portability cost.
5. **Web stack** (Tauri + WebGL/WebGPU canvas, or Electron). Cross-platform "for free," translucent windows supported, click-through supported on all three. Trade-off: huge runtime, slow startup, Python ↔ JS bridge for the brain integration.
6. **pygame / pyglet / arcade / raylib** in a transparent overlay window. Game-engine ergonomics, but transparent always-on-top click-through is poorly supported in most.

**Evaluation matrix** (research must fill this in for each candidate):

| Capability | Stay | QtQuick | QOpenGLWidget | Native+Qt | Web | Game-engine |
|---|---|---|---|---|---|---|
| Per-pixel transparency | | | | | | |
| Click-through-when-not-painted | | | | | | |
| Always-on-top + don't-steal-focus | | | | | | |
| Multi-monitor, per-screen DPR | | | | | | |
| Smooth motion @ 4K 144Hz (evidence) | | | | | | |
| Cross-platform (mac/lin/win) | | | | | | |
| All current features preserved | | | | | | |
| Migration LoC (estimate) | | | | | | |
| Adds runtime / install footprint | | | | | | |
| Risk of platform-specific bugs | | | | | | |

Output of phase 7: a recommendation paragraph at the end of the plan (or a separate `plans/qt-it-backend-recommendation.md`) that names the prudent choice and shows the evidence. If the recommendation is "migrate," phase 7 itself does NOT do the migration — it produces a follow-up plan (`plans/qt-it-migration.md`) that owns the actual move.

## Failure modes to anticipate

- **The "shadow" is the speech bubble.** Bubble paints rotated text via `head_world_position()` + `body_angle()` on its own paintEvent timing. If buddy paints at vsync N (θ_N) and bubble paints at vsync N+1 (θ_{N+1}), DWM composites them in the same frame at slightly different angles → "rotating shadow."
- **The "shadow" is the weather sky panel.** Same risk: weather reads buddy bounds; if the cached bounds are one tick stale, sky tilts wrong.
- **Move-then-update gap.** Every tick, `widget.move()` is called BEFORE `update()`. The window position changes immediately at the WM level; the paint into the new position lands one Qt event-loop iteration later. Stale-content-at-new-position is visible for ~7 ms (one vsync) per tick.
- **`update()` coalescing aliases state.** Qt collapses multiple `update()` calls per event-loop iteration. So at 166 Hz pump and 144 Hz refresh, some pumps' updates merge — paint sees only the LATEST tick's state, hiding intermediate states the lerp could have used.
- **`self.pos()` returns last-committed position, not necessarily latest `move()` arg.** WM round-trip lag. Reading `self.pos()` in `_build_transform` for the residual calculation may give stale data → sub-pixel residual is wrong by 1+ px.
- **`setMask()` round-trip lag.** Comment in code already calls out ~1 ms WM round-trip. If mask updates lag paint, briefly clipped silhouettes flash on rotation.
- **AABB resize churn.** Velocity-aware `pos_slack` recomputes every tick. Even rounding errors in the velocity calc can change the slack by 1 px tick-to-tick → real WM resize every tick → WM dispatch overhead → frame stutter.
- **DWM frame queuing.** Windows DWM may queue 1–2 frames. Our paint may show 7–14 ms after we issue it.
- **Pump-vs-refresh beat at 22 Hz.** Mathematically unavoidable at 166 Hz pump + 144 Hz refresh without syncing one to the other.
- **PreciseTimer slip on Windows 11.** Even PreciseTimer can stall ~1–2 ms under GIL contention. A skipped tick = lerp α extrapolates past 1, then snaps back when the next tick fires — a one-frame visible discontinuity.
- **WA_TranslucentBackground UpdateLayeredWindow path.** Layered windows on Windows have separate composition semantics; may not respect vsync the way normal windows do, creating tearing on rapid motion.
- **`time.monotonic()` is sampled at paintEvent execution time, NOT the target vsync time.** The lerp is for "now," but the painted frame won't appear until the next DWM composite, which is later. Compounding latency.
- **4K + DPR scaling fillrate.** Master pixmap is ~23 MB; paint is 4× the bytes of 1080p. If paint body > 7 ms, frames drop and motion stutters regardless of all the lerp work above.
- **Single-clock refactor introduces inconsistency between hit-test and paint.** `_invert_widget_to_art` (mouse hit-test) currently uses `_build_transform` which reads `time.monotonic()`. If hit-test runs at a different "now" than paint, click-to-art mapping diverges from the visible buddy by a sub-pixel amount. Probably benign but worth a test.
- **Defer-move-into-paint regression.** If we defer `widget.move()` into paintEvent, signals like `position_changed` stop firing per-tick — followers that depend on the per-tick emit may stop tracking. Need to keep the signal firing on the tick path even if the actual `move()` is deferred.
- **Backend evaluation bias.** Easy to fall into "migrate to a shiny GPU stack" enthusiasm when the actual smoothness ceiling is surmountable in-place. Evaluation must include the "stay" option with phase 1–6 fixes applied as the baseline; migration only wins if it materially beats the patched baseline.
- **Translucent QOpenGLWidget on Windows is a known minefield.** Historical Qt bugs (QTBUG-43282, QTBUG-50414 family) — translucent GL widget either flickers, fails to composite, or only works in specific driver combinations. Research must surface current state in PySide6 / Qt 6.x, not historical anecdote.
- **QtQuick translucent window on Wayland.** Compositor support varies (KWin works, GNOME's Mutter has rough edges, sway/wlroots needs specific protocols). Don't assume parity with X11.
- **macOS NSWindow `setOpaque:NO` + always-on-top + don't-steal-focus interaction.** Already a delicate setup in the current Qt code (see `apply_macos_stay_visible` reference). Whatever backend we move to must reproduce this triplet without Qt's helpers.
- **Tauri/Electron click-through on macOS** requires `setIgnoreMouseEvents(true, {forward: true})` semantics — but then the buddy itself can't be grabbed. Need per-pixel hit-test wired through to the native window manager. Web-stack candidates need to demonstrate this works.
- **Cross-platform DPR / vsync APIs differ wildly.** Whatever solves the smoothness problem on Windows (DwmFlush, DXGI WaitForVBlank) has macOS (`CVDisplayLink`) and Linux (Wayland presentation-time, X11 GLX_OML_sync_control) equivalents that may behave differently. Evaluation must measure on all three, not just extrapolate from Windows.
- **Migration leaks into senses/brain because of tight coupling.** If overlay/buddy_window APIs are referenced widely outside `tokenpal/ui/qt/`, a backend swap may force changes in `brain/`, `actions/`, `senses/`. Audit call graph before recommending migration.

## Done criteria

- The "shadow" is identified: which window paints it, why it's stale, fixed.
- All windows that follow the buddy lerp to the SAME target timestamp per composite frame. Verified by `TOKENPAL_PAINT_TRACE=1` showing matching `theta_used` and `pos_used` across the buddy and every follower window per Δt < 1 ms.
- `TOKENPAL_TICK_PROFILE=1` shows paint body p99 < 7 ms (one 144 Hz vsync) at zoom 2x with the buddy in motion.
- Move/update ordering is fixed: no visible 1-frame stale-content-at-new-position artifact during fast motion.
- AABB resize event rate is < 30/sec during sustained motion (not 166/sec). Verified via instrumentation.
- All 246 qt/buddy/paint/physics tests still pass.
- User does an honest 30-second drag/fling/zoom test on the 4K @ 144Hz display and reports no perceptible jitter and no shadow.
- A short README section or doc note is added explaining the paint-clock contract so future follower additions don't reintroduce the bug.
- **Backend recommendation written and signed off**: a section (or sibling file) that fills in the phase 7 evaluation matrix with measured/researched evidence for every candidate, names the prudent choice (stay vs. migrate, and if migrate, which target), and shows the math. If the recommendation is "migrate," a follow-up plan `plans/qt-it-migration.md` exists owning the actual move (this plan does not execute the migration itself).
- Cross-platform smoke test: whatever fixes land work on macOS (M-series) and Linux (Wayland or X11) without regression. If a fix is Windows-only, that's called out explicitly and macOS/Linux either get a parallel fix or a "known-acceptable" note.
- Every current feature still works after fixes: per-pixel transparency, click-through, always-on-top, drag/fling/zoom, speech bubble follower, weather sky, chrome/resize-grip, dock, tray, voice modal, multi-monitor, Textual fallback. Manual verification list, not just test pass count.

## Parking lot

(empty at start)
