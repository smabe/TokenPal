# qt-it research findings

Consolidated output of the post-approval research pass for `plans/qt-it.md`. Six parallel specialists. Implementation happens in a new session — this doc is the handoff.

---

## TL;DR (read this first)

1. **The "rotating shadow" is `BuddyRainOverlay` (`tokenpal/ui/qt/weather.py:994–1073`).** Its window anchor is updated at the buddy's `position_changed` rate (~60–166 Hz) but its `paintEvent` is triggered by `SkyWindow`'s independent 30 Hz QTimer. Particles render in *world-absolute* coords; when the buddy rotates between anchor refreshes, the cached anchor is stale → the painted particle field appears to rotate relative to the buddy. The overlay never re-samples buddy angle at paint time. **Other followers (bubble, dock, grip) sample fresh angle at signal time and store it before paint — they are NOT the culprit.**

2. **Yes, there is a STRUCTURAL ceiling on the current stack on Windows.** `QWidget + WA_TranslucentBackground + frameless` routes through `WS_EX_LAYERED` + `UpdateLayeredWindow`. That path has no swapchain, no MPO promotion, no `WaitForVBlank` hook, and bitmaps round-trip GPU → system memory → GPU per paint for DWM compositing. The 22 Hz beat between our 166 Hz pump and 144 Hz refresh + DWM's 1–3 frame queue cannot be vsync-aligned from inside this path. This is sourced, not opinion.

3. **The prudent move is two-phase, not one-shot migration.** Phase A (tactical, in-place): fix the rain-overlay desync, collapse multi-window follower paint into one window, call `DwmFlush()` to serialize against composite, drop pump to 144 Hz to kill the beat, switch the buddy's repaint to synchronous `repaint()` to bypass `update()` coalescing. Phase B (only if A doesn't get us to "smooth at 4K @ 144Hz"): migrate to a **single-window QtQuick** architecture where all followers become child `QQuickItem`s of one `QQuickWindow` — this is the only configuration that escapes both the layered-window ceiling AND QtQuick's multi-window scene-graph fallback. Multi-window QtQuick is *not* an improvement and may be worse.

4. **Disqualified candidates** (research evidence, not preference): translucent `QOpenGLWidget` on Windows (long-running QTBUG family — flicker on resize, frameless+translucent repaint failures); web stack on Wayland (Tauri click-through has no effect; Wayland has no global coordinate system for per-pixel hit-test); web stack on macOS App Store (Tauri transparency requires a private-API flag that bans App Store distribution); all game engines on Wayland (no `wlr-layer-shell` support); native+Qt hybrid (2500–4000 LoC migration, macOS NSWindow brittleness, doesn't solve GNOME-Wayland gap that Qt also can't solve).

5. **Cross-platform reality**: GNOME-on-Wayland's missing `wlr-layer-shell` (Mutter has refused to add it) is a hard wall for "always-on-top above fullscreen + per-pixel click-through" — *every* candidate (including the current stack and QtQuick) has the same yellow cell there. This is a property of the protocol, not the toolkit. Not a regression for any path.

---

## Phase A — In-place tactical fixes (recommended first)

These should land before any backend migration is considered. Combined evidence from agents 1, 4, and 5.

### A1. Fix `BuddyRainOverlay` paint sync (the shadow)

**File**: `tokenpal/ui/qt/weather.py:994–1073`. Currently:
- Window anchor cached via `reanchor()` called from `position_changed` (~60 Hz)
- Paint triggered by `SkyWindow._on_tick()` direct callback at 30 Hz (`weather.py:823–828`)
- `paintEvent` reads particles in world-absolute coords, never re-samples buddy angle
- When buddy rotates without translating, anchor stays stale and particles appear to rotate relative to him

**Fix**: re-sample the buddy's current world transform inside `BuddyRainOverlay.paintEvent` itself, OR drive the overlay's `update()` from the buddy's `position_changed` (same trigger as the other followers, which audit shows are NOT desyncing). The 30 Hz sky-tick callback should advance simulation only, not trigger overlay paint.

### A2. Collapse follower paint into a single window

**Source**: agent 1 explicitly: *"Collapse all followers into a single translucent QWidget so they share one WM_PAINT and cannot drift relative to each other. This is the single largest fix available without leaving the QWidget stack."*

Each `QWidget` toplevel today (buddy, bubble, sky, rain overlay, chrome, dock) gets its own `QBackingStore`, its own `flush()`, and its own DWM present. Two windows drawn from "the same tick" can land on different DWM composites. Collapsing them into one widget that paints the buddy + followers in one `paintEvent` removes inter-window present desync at the source.

**Cost**: meaningful refactor — followers become *paint regions* inside a single buddy-overlay widget, not separate windows. Click-through routing changes (one mask, multiple paint regions). But this is the largest leverage available without leaving QWidget.

### A3. Drop pump rate to 144 Hz to match display

**Current**: 6 ms (~166 Hz) PreciseTimer pump → 144 Hz refresh → beat at gcd ≈ 22 Hz, in human flicker-perception range.

**Fix**: pump at `1000 / refreshRate` ms (read `QScreen.refreshRate()`). Physics still runs at 240 Hz internally via the accumulator — this only changes the pump's outer cadence. Eliminates the beat.

**Source**: agent 5 (Alen Ladavac, GDC 2019 "The Elusive Frame Timing"): *"once your sample rate and refresh rate are coprime, lerping to `now` produces stair-stepping at the beat period regardless of how smooth the underlying physics is."*

### A4. Switch buddy paint to synchronous `repaint()` after physics step

**Source**: agent 5 — `QWidget.update()` coalesces multiple calls per Qt event-loop iteration into one `paintEvent` (`qtbase/src/gui/kernel/qwidget.cpp` ~line 1960 in 6.7, `WA_PendingUpdate` flag). At 166 Hz pump + 144 Hz paint, ~1 in 7 pumps' state is silently aliased away. `repaint()` is synchronous and bypasses coalescing.

**Cost**: paint runs more often, on the GUI thread, per pump. Mitigated by A3 dropping pump rate to 144 Hz.

### A5. Call `DwmFlush()` once per pump to serialize against next composite

**Source**: agent 1, agent 5. `DwmFlush()` blocks until the next DWM composition; the only reliable "next composite happens NOW" signal for layered windows. Qt does not call it. Available via ctypes:

```python
import ctypes
ctypes.windll.dwmapi.DwmFlush()
```

Reduces but does not eliminate the 1–3 frame DWM queue latency variance.

### A6. Predict-to-vsync, not lerp-to-now

**Source**: agent 5, multiple. Canonical smooth-animation loops sample state at the **predicted next-vsync timestamp**, not `time.monotonic()` at paint time. iOS `CADisplayLink` exposes `targetTimestamp` for exactly this. Apple WWDC23 session 10075 is explicit: *"animate to `targetTimestamp`, never `CACurrentMediaTime()`."* Casey Muratori (Handmade Hero day 029): predict the flip time and ask gameplay "where will you be at T+flip" — sampling at `now` bakes in one full frame of latency.

**For TokenPal**: in `_build_transform`, replace `delta_s = time.monotonic() - self._last_step_ts` with `delta_s = (predicted_vsync_ts) - self._last_step_ts` where `predicted_vsync_ts = last_dwm_composite_ts + 1/refresh_rate`. We can sample DWM composite timestamps via `DwmGetCompositionTimingInfo`.

### A7. Snap velocity-aware AABB slack to a step function

**Source**: failure-mode in plan + my own analysis. Current `pos_slack = ceil(|v| × FIXED_DT) + 1` recomputes every tick under acceleration → can change by 1 px tick-to-tick → real WM resize at up to 166 Hz. Round to nearest 4 px so resize fires only on slack-crossing.

### A8. The "ruler scroll" smoke test

**Source**: agent 5. Before and after each phase-A change, run this test as a falsifiable smoothness gate:

> Translate a 1-pixel-wide vertical white line horizontally across a 4K @ 144 Hz window at exactly 144 px/sec. Capture with iPhone 14+ slo-mo (1000 fps) for 5 seconds. Pass criteria: line position vs capture-frame is a perfectly straight line of slope (144/1000) px-per-frame. Plateaus = dropped present. Double-steps = doubled present.

Standalone `tests/manual/ruler_scroll.py` — 30 lines of QWidget. Reference: testufo.com/framerates.

---

## Phase B — Backend migration (only if Phase A insufficient)

If after Phase A the buddy still doesn't pass the ruler-scroll test on the 4K @ 144Hz display, the structural ceiling has won and we migrate.

### Recommendation: **Single-window QtQuick** (no other candidate is competitive)

**Why QtQuick beats the alternatives**:

| Candidate | Verdict | Reason |
|---|---|---|
| Stay (post-Phase-A) | Baseline | If passes ruler-scroll, ship it. Don't migrate. |
| **Single-window QtQuick** | **Recommended migration target** | DirectComposition + flip-model swapchain on Windows (escapes `UpdateLayeredWindow` ceiling). Threaded scene-graph render loop with `swapInterval=1`. `QQuickWindow.frameSwapped` signal for vsync timing. Cross-platform native: macOS Metal, Linux EGL/Vulkan, Windows D3D11/12. |
| Multi-window QtQuick | Disqualified | Multi-window mode falls back to single system-timer driver — loses the threaded scene-graph benefit. TokenPal's 4+ follower windows would land here. Migrating to multi-window QtQuick spends 1500–2500 LoC for *no* smoothness gain. |
| Translucent QOpenGLWidget | Disqualified on Windows | QTBUG-46634 (flicker on resize — TokenPal resizes every tick), QTBUG-54734 (frameless translucent repaint failures), QTBUG-89688 (translucent + resize flicker). Qt's own docs warn: *"Putting other widgets underneath and making the QOpenGLWidget transparent will not lead to the expected results."* |
| Native + Qt hybrid | Too costly | 2500–4000 LoC across mac (CAMetalLayer), Win (DirectComposition), Linux (wlr-layer-shell + X11 OR). macOS NSWindow stay-visible across Spaces is OS-update-fragile. Doesn't solve GNOME-Wayland gap. |
| Web stack (Tauri/Electron) | Disqualified | Tauri lacks `forward: true` for cursor events (cursor poll required); on Wayland `setIgnoreCursorEvent` has no effect (Wayland has no global coords); Tauri transparent-on-macOS requires `macos-private-api` flag → bans App Store; Electron 150 MB footprint. |
| Game engine (pygame/pyglet/raylib) | Disqualified | None offers transparent + always-on-top + per-pixel click-through on all three platforms. Shijima-Qt (the only successful cross-platform desktop pet) explicitly migrated *away* from this approach to Qt6. |

### Single-window QtQuick architecture (the load-bearing constraint)

**Critical**: the migration must collapse buddy + bubble + weather + chrome + dock into ONE `QQuickWindow` with child `QQuickItem`s. Multi-window QtQuick falls back to the single-timer render driver (Qt forum confirmed: *"with more than one QQuickWindow on screen the threaded loop falls back to a single system-timer-driven loop"*) and provides no smoothness benefit over staying.

This is essentially Phase A's collapse-into-one-widget step (A2) repeated at the Quick layer. Phase A2 is therefore **forward-compatible** with Phase B — the architecture you build for A2 ports cleanly to QtQuick later. **A2 is the migration's foundation; do A2 first regardless of whether B happens.**

### Migration cost

- **Single-window QtQuick port** of buddy + followers: ~1500–2500 LoC delta (per agent 2)
- **Custom `QQuickItem` + `updatePaintNode` + texture upload** for the master sprite (the textured-quad path; `QQuickPaintedItem` is QPainter-on-FBO and defeats the purpose)
- **Hit-test inversion** (`_invert_widget_to_art`) and the lerp clock survive unchanged
- **Tray + dialogs + voice modal** stay on QWidget — Qt allows mixing QWidget and QQuickWindow in one app

### Migration plan (defer to a follow-up plan if Phase B becomes necessary)

If we go to Phase B, the actual migration gets its own plan: `plans/qt-it-quick-migration.md`. This research doc just establishes the *target*, not the path.

---

## Per-agent findings (deep dive, for next session reference)

### Agent 1 — Qt/Windows DWM smoothness ceiling

**Verdict: STRUCTURAL CEILING confirmed.** Three structural limits no Qt-side fix can clear:

1. **No swapchain → no `WaitForVBlank` / `SetMaximumFrameLatency` hook.** Layered windows don't get a swapchain. App cannot tell DWM "present this frame at vsync N." Frames picked up at DWM's discretion.
2. **MPO promotion impossible.** Hardware overlay requires flip-model swapchain; layered windows can't qualify. 1-frame DWM minimum latency is hard floor.
3. **GPU → sysmem → GPU bitmap round-trip per paint.** At 4K with ~23 MB master pixmap, structural fillrate tax. Non-deterministic stalls even when paint body fits in 7 ms.

**Highest-leverage in-place mitigations** (Phase A):
- `DwmFlush()` per pump to serialize against composite
- Collapse all followers into single QWidget (one WM_PAINT, no inter-window drift)
- Drop pump to 144 Hz to kill 22 Hz beat

**Structural fix path**: `QQuickWindow` with `setColor(Qt.transparent)` routes through DirectComposition + flip-model swapchain ([qt/qtdeclarative commit 989592b](https://github.com/qt/qtdeclarative/commit/989592bf5970471a7ff32a7b740172c8688e2171)), gets `frameSwapped` for vsync timing, can be MPO-promoted, avoids GDI sysmem round-trip.

### Agent 2 — Qt-internal alternatives evaluation

**QtQuick: viable but risky in TokenPal's current architecture.** Real GPU scene graph would beat CPU rasterization for fillrate, but TokenPal's multi-window architecture lands you exactly in the documented threaded-loop fallback case where the smoothness benefit evaporates. Plus 1.5–2.5K LoC migration cost with no published evidence the target works at 4K@144Hz with translucency. **Mitigation: collapse to single window first.**

**QOpenGLWidget: disqualified for Windows.** Combination of `WA_TranslucentBackground` + frameless + per-tick widget resize + QOpenGLWidget on Windows is the intersection of every long-running Qt bug:
- QTBUG-46634 — flicker on resize (TokenPal resizes every tick)
- QTBUG-54734 — frameless translucent repaint failures
- QTBUG-89688 — translucent + stylesheet flicker on shrink
- QTBUG-51093 / QTBUG-18167 — black-frame flicker on translucent
- Qt's own docs: *"Putting other widgets underneath and making the QOpenGLWidget transparent will not lead to the expected results."*

### Agent 3 — Non-Qt backend evaluation

**Native + Qt hybrid: too costly.** 2500–4000 LoC. macOS pinning fragile. GNOME-Wayland gap unsolvable.

**Web stack: disqualified for cross-platform.** 
- Tauri click-through has no `forward: true` (open issues #6164, #13070); workaround is Rust-side ~60 Hz cursor-position poll.
- Tauri on macOS requires `macos-private-api` flag → bans App Store.
- Tauri/Electron click-through on Wayland: *"`setIgnoreCursorEvent` has no effect on Wayland because Wayland has no global coordinate system."*
- Electron has documented Windows focus-related click-through breakage (#33281, #23042).
- Adds a JS runtime, npm/cargo build, second language. Pump→paint latency through IPC channel makes the smoothness goal *harder*, not easier.

**Game engines: disqualified.** No engine offers transparent + always-on-top + per-pixel click-through on all three platforms. Shijima-Qt (the only successful cross-platform desktop pet) migrated *away* from this approach **to Qt6**. That's the data point.

### Agent 4 — Follower call-graph audit

**Smoking gun**: `BuddyRainOverlay` (`weather.py:994–1073`).
- Window anchor sampled at `position_changed` (~60 Hz), via `_reanchor_weather` → `reanchor()` (overlay.py:351, 1040–1056)
- Paint triggered by `SkyWindow._on_tick()` direct method-ref callback at 30 Hz (overlay.py:348, weather.py:823–828) — NOT a signal connection
- Particles in world-absolute coords; paint reads `self._sim.particles` and renders without re-sampling buddy angle
- Result: when buddy rotates between anchor refreshes, particles appear to rotate relative to him

**Other followers are clean**: SpeechBubble (line 252–270, signal-driven set_pose), DockMock (line 67–91), BuddyResizeGrip (_chrome.py:148–201) all sample buddy angle fresh at signal-emit time and store as instance state before paint. Bubble + dock + grip are NOT the shadow.

**`_build_transform()` calls `time.monotonic()` at buddy_window.py:598** every paint and every call to `body_angle()` / `head_world_position()` / `foot_world_position()` / `art_frame_point_world()`. Multiple windows reading these methods sample wall-clock independently → inter-window time desync. Mitigation: a `_paint_clock` shared by all windows.

**No `repaint()` calls** anywhere in `tokenpal/ui/qt/*.py` — all async via `update()`.

**4 connections** to `position_changed`: bubble (overlay.py:287), dock (288), weather/rain reanchor (351), grip (358).

### Agent 5 — Inner loop specialist (the "what canonical smooth loops do" deep dive)

**Five-stage canonical loop** (Godot, Unreal, iOS CADisplayLink, Chromium):

1. **Predict next vsync target time** — sample state AT predicted photon time, not `now`
2. **Lerp/extrapolate to that target**
3. **Submit GPU work**
4. **Wait on vsync / present** — `Present(1, 0)`, `glXSwapBuffers`, `CVDisplayLinkOutputCallback`. Loop driven *by* this callback, not a software timer.
5. **Sample input as late as possible** — after vsync wait returns. NVIDIA Reflex "Render Submission Throttling" does exactly this.

**TokenPal does none of stages 1, 4, 5.** Samples at `now` (1 frame latency baked in). Driven by software QTimer not vsync. Input sampling tied to whenever Qt routes events.

**Failure modes specifically caused by stage gaps**:
- Beat-frequency micro-stutter at gcd(166, 144) ≈ 22 Hz — visible as ~45 ms periodic pause-pause-jump
- DWM frame-queue latency variance (1–3 frames; layered windows bypass DXGI flip model — *no vsync alignment guarantee*)
- Inter-window desync ("rotating shadow") — each toplevel `QWidget` has its own `QBackingStore`, own `flush()`, own DWM present

**Load-bearing details NOT in API docs**:
- `QWidget::update()` coalescing — `qwidget.cpp` ~line 1960 in 6.7, `WA_PendingUpdate` flag dance. ~1 in 7 pumps' state thrown away at 166 Hz pump + 144 Hz paint
- `QWidget::repaint()` is synchronous, your only escape from coalescing aliasing
- `QBackingStore::flush()` on Windows = synchronous BitBlt to UpdateLayeredWindow. No `Present(1, …)`. No vsync wait. Structural reason layered Qt widgets can't vsync-align.
- `WA_NoSystemBackground` + `WA_TranslucentBackground` without explicit clear → previous-frame ghosting. Look at `QWidgetBackingStore::beginPaint`.
- PreciseTimer Windows floor 1 ms via `timeSetEvent`; DPC dispatch jitter typical 2–4 ms, p99 5–15 ms under contention (Bruce Dawson, randomascii)
- DWM frame-queue depth Windows 11: 2 frames default for normal flip-model. `DwmFlush()` blocks until next composition. `DCompositionWaitForCompositorClock` (Win10 19H1+) is the modern replacement. Qt calls neither.
- `QWidget::move()` is `SetWindowPos` and returns before WM has moved the window. `pos()` reads cached state.
- `QGuiApplication` does not connect to `QScreen` vsync on Windows. `QQuickWindow` (Quick, not Widgets) hooks the render thread to `IDXGIOutput::WaitForVBlank` via the RHI backend — *that's why Quick is smoother out of the box than Widgets*.
- `setMask()` round-trip — WM applies on its own schedule. Canonical fix: keep mask static, do per-pixel clipping in alpha channel.

**Plan-relevant**: phase 5 ("drive pump from `QScreen::vsync` if available") will not find one for `QWidget` on Windows — it doesn't exist. That's why Phase A4 goes for `repaint()` + `DwmFlush()` instead.

### Agent 6 — Cross-platform feature parity matrix

| Candidate | macOS | Wayland | X11 | Win11 |
|---|---|---|---|---|
| Stay | Y | P | Y | Y (with ceiling) |
| QtQuick | Y | P | Y | Y |
| QOpenGLWidget | P | P | Y | P |
| Native+Qt hybrid | Y | P | Y | Y |
| Web stack | P | **N** | P | Y |
| Game engine | P | **N** | P | P |

**No candidate has zero red cells.** GNOME-Wayland is universal yellow because Mutter has refused `wlr-layer-shell` ([gitlab.gnome.org/GNOME/mutter#973](https://gitlab.gnome.org/GNOME/mutter/-/issues/973)) and Wayland has no global coordinate system. This is a protocol/compositor wall, not a toolkit choice.

**Cleanest set**: Stay, QtQuick, Native+Qt hybrid — all have only the unavoidable Wayland-GNOME yellow.

**Disqualifying reds**: Web stack on Wayland (click-through removed entirely); web stack on macOS App Store (private-API flag); game engines on Wayland.

---

## Proposed plan edits for next session

To apply to `plans/qt-it.md` at the start of the implementation session:

### Goal update
> **Strike** the existing dual-deliverable framing. **Replace** with: "Phase A — fix the `BuddyRainOverlay` paint sync (the rotating shadow), then collapse follower paint into one window, drop pump to 144 Hz, switch to `repaint()`, call `DwmFlush()`, predict-to-vsync. Pass the ruler-scroll smoke test on the 4K @ 144Hz display. If it still doesn't pass, Phase B = single-window QtQuick migration via a follow-up plan `plans/qt-it-quick-migration.md`."

### Files to touch — replace with concrete list
- `tokenpal/ui/qt/weather.py` (lines 994–1073) — fix `BuddyRainOverlay` paint sync
- `tokenpal/ui/qt/overlay.py` (line 348) — possibly remove the direct-callback wiring; route through `position_changed`
- `tokenpal/ui/qt/buddy_window.py` — collapse-into-one-widget refactor (A2), pump rate (A3), `repaint()` switch (A4), `DwmFlush()` call (A5), predict-to-vsync (A6), AABB slack snap (A7)
- `tokenpal/ui/qt/_paint_clock.py` (NEW) — shared paint-time clock so all paint paths sample at the same predicted vsync timestamp
- `tests/manual/ruler_scroll.py` (NEW) — falsifiable smoothness smoke test (A8); first thing built in phase A

### Failure modes to add
- **Collapsing followers into one widget changes click-through routing.** Each follower had its own mask; now one mask must encode all painted regions. Need region-builder that ORs the buddy's silhouette + bubble's pill + weather's sky rect + chrome's grip + dock's bar.
- **`DwmFlush()` is Windows-only.** Phase A5 needs a no-op on macOS/Linux (or use `CVDisplayLink` / `presentation-time` equivalents — separate scope).
- **Predict-to-vsync needs a DWM composite-time source on Windows.** `DwmGetCompositionTimingInfo()` returns last vsync timestamp + period. ctypes call.
- **Single-window QtQuick is non-negotiable for Phase B.** Multi-window QtQuick falls back to single-timer driver — same problem we already have, plus 2K LoC of churn. If anyone proposes "let's just QtQuick the buddy and keep separate windows for followers," push back hard.

### Done criteria additions
- Ruler-scroll smoke test passes on the 4K @ 144Hz display (gates phase A → ship vs phase B)
- BuddyRainOverlay confirmed gone as shadow source via `TOKENPAL_PAINT_TRACE` instrumentation
- Each Phase-A change measured independently via the smoke test (so we know which lever moved the needle, not just the cumulative fix)

---

## Citations (consolidated)

### Microsoft / Windows DWM
- [UpdateLayeredWindow API — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow)
- [Layered Windows with Direct2D — MSDN Magazine](https://learn.microsoft.com/en-us/archive/msdn-magazine/2009/december/windows-with-c-layered-windows-with-direct2d)
- [High-Performance Window Layering Using Composition Engine — MSDN Magazine](https://learn.microsoft.com/en-us/archive/msdn-magazine/2014/june/windows-with-c-high-performance-window-layering-using-the-windows-composition-engine)
- [DirectComposition click-through in transparent areas — MS Q&A](https://learn.microsoft.com/en-au/answers/questions/2153247/directcomposition-click-through-in-transparent-are)
- [DWM latency analysis — lofibucket](https://www.lofibucket.com/articles/dwm_latency.html)
- [Present Latency, DWM, Waitable Swapchains — jackmin](https://jackmin.home.blog/2018/12/14/swapchains-present-and-present-latency/)
- [Input Latency Platform Considerations — Darpinian](https://james.darpinian.com/blog/latency-platform-considerations/)
- [Layered window performance — MS public.win32](https://microsoft.public.win32.programmer.ui.narkive.com/fohkGcAG/layered-window-performance)

### Qt source / docs / bugs
- [Qt qwindowswindow.cpp (dev branch)](https://github.com/qt/qtbase/blob/dev/src/plugins/platforms/windows/qwindowswindow.cpp)
- [QOpenGLWidget Class — Qt 6](https://doc.qt.io/qt-6/qopenglwidget.html)
- [QQuickWindow Class — Qt 6](https://doc.qt.io/qt-6/qquickwindow.html)
- [Qt Quick Scene Graph — Qt 6.11](https://doc.qt.io/qt-6/qtquick-visualcanvas-scenegraph.html)
- [PySide6 QQuickWindow](https://doc.qt.io/qtforpython-6/PySide6/QtQuick/QQuickWindow.html)
- [Render Thread Animations in Qt Quick 2.0 — Qt blog](https://www.qt.io/blog/2012/08/20/render-thread-animations-in-qt-quick-2-0)
- [QtDeclarative DComp translucent commit](https://github.com/qt/qtdeclarative/commit/989592bf5970471a7ff32a7b740172c8688e2171)
- [QTBUG-101047 — Win11 obscured-background lines](https://bugreports.qt.io/browse/QTBUG-101047)
- [QTBUG-46634 — QOpenGLWidget flicker on resize](https://bugreports.qt.io/browse/QTBUG-46634)
- [QTBUG-54734 — frameless translucent + QOpenGLWidget repaint](https://bugreports.qt.io/browse/QTBUG-54734)
- [QTBUG-89688 — translucent + stylesheet flicker on resize](https://bugreports.qt.io/browse/QTBUG-89688)
- [QTBUG-51093 — black-frame flicker translucent + GL](https://bugreports.qt.io/browse/QTBUG-51093)
- [QTBUG-34064 — translucent broken before widget polish](https://bugreports.qt.io/browse/QTBUG-34064)
- [QTBUG-28214 — translucent QQuickView issues](https://bugreports.qt.io/browse/QTBUG-28214)
- [Qt Forum: WindowTransparentForInput broken on Wayland](https://forum.qt.io/topic/154266/windowtransparentforinput-not-worked-on-wayland)
- [Qt Forum: multi-window threaded scene-graph fallback](https://forum.qt.io/topic/154956/how-to-provide-multiple-render-threads-for-scene-graph-for-more-than-one-qquickwindow-instances-on-screen)
- [Qt Forum: QOpenGLWidget transparency blending](https://forum.qt.io/topic/158453/not-getting-correct-blending-when-using-qopenglwidget-and-a-translucent-window)
- [donutwindow: shaped Qt Quick window](https://github.com/ryanmcalister/donutwindow)
- [qmlbench](https://github.com/CrimsonAS/qmlbench)

### Wayland / GNOME / freedesktop
- [GNOME Mutter: implement layer_shell protocol (#973)](https://gitlab.gnome.org/GNOME/mutter/-/issues/973)
- [SDL: always-on-top window wayland (#5779)](https://github.com/libsdl-org/SDL/issues/5779)
- [wayland.app: wlr-layer-shell-unstable-v1 protocol](https://wayland.app/protocols/wlr-layer-shell-unstable-v1)

### macOS
- [NSWindow ignoresMouseEvents — Apple Developer](https://developer.apple.com/documentation/appkit/nswindow/1419354-ignoresmouseevents)
- [Translucent overlay window on macOS in Swift — gaitatzis](https://gaitatzis.medium.com/create-a-translucent-overlay-window-on-macos-in-swift-67d5e000ce90)
- [Apple Developer: window visible across Spaces + fullscreen](https://developer.apple.com/forums/thread/26677)
- [Apple WWDC23 session 10075 — Variable Refresh Rate Animation Contract]

### Web stack (Tauri/Electron)
- [tauri-apps/tauri#6164 — feat: forward option for setIgnoreCursorEvents](https://github.com/tauri-apps/tauri/issues/6164)
- [tauri-apps/tauri#2090 — ignore mouse on transparent areas](https://github.com/tauri-apps/tauri/issues/2090)
- [tauri-apps Discussion #11507 — forward mouse to underneath windows](https://github.com/tauri-apps/tauri/discussions/11507)
- [tauri-apps/tauri#6162 — window properties ignored on Linux](https://github.com/tauri-apps/tauri/issues/6162)
- [bgschiller/tauri-transparency-demo — cursor-poll workaround](https://github.com/bgschiller/tauri-transparency-demo)
- [Why I Chose Tauri v2 for a Desktop Overlay — manasight blog](https://blog.manasight.gg/why-i-chose-tauri-v2-for-a-desktop-overlay/)
- [Tauri vs Electron: performance, bundle size — gethopp](https://www.gethopp.app/blog/tauri-vs-electron)
- [Tauri Webview Versions](https://v2.tauri.app/reference/webview-versions/)
- [pytauri/pytauri](https://github.com/pytauri/pytauri)
- [Electron #30808 — setIgnoreMouseEvents forwarding bug](https://github.com/electron/electron/issues/30808)
- [Electron #33281 — forwarding fails for non-electron focus](https://github.com/electron/electron/issues/33281)
- [Electron #23042 — click-through frameless transparent broken >6.1.9](https://github.com/electron/electron/issues/23042)

### Game engines
- [pygame.Window — pygame-ce docs](https://pyga.me/docs/ref/window.html)
- [pyglet windowing styles docs](https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html)
- [pyglet #874 — Overlay style not working on Linux](https://github.com/pyglet/pyglet/issues/874)
- [pyglet #693 — Transparent window not working on Win11](https://github.com/pyglet/pyglet/issues/693)
- [pyglet #1271 — blending bug Labels/Rectangles (2025)](https://github.com/pyglet/pyglet/issues/1271)
- [Shimeji-ee — Kilkakon (Windows-only)](https://kilkakon.com/shimeji/)
- [Shijima-Qt — cross-platform Shimeji on Qt6](https://github.com/pixelomer/Shijima-Qt)
- [glfw PR #2061 — wlr-layer-shell window hint (open)](https://github.com/glfw/glfw/pull/2061)

### Frame pacing / inner-loop references
- Casey Muratori, *Handmade Hero* day 029 + day 044 (input-to-photon, frame timing)
- Tim Sweeney, GDC 2017 "State of Unreal" (input pipeline depth)
- Alen Ladavac, GDC 2019 "The Elusive Frame Timing"
- NVIDIA Reflex SDK Programming Guide, §"Render Submission Throttling"
- Bruce Dawson, randomascii.com — "Sleep Variation Investigated", "Windows Timer Resolution"
- Raymond Chen, "The Old New Thing" — layered-window composition semantics
- Blur Busters / testufo.com — reference motion test target
- Apple WWDC23 session 10075 — variable refresh rate animation contract

### TokenPal codebase references
- `C:\Users\Smabe\tokenpal\plans\qt-it.md` — the plan being researched
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\buddy_window.py` (paintEvent line ~912; `_build_transform` line ~572 calling `time.monotonic()` line 598; `_recompute_geometry` line ~404; position_changed signal line ~462)
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\weather.py` (BuddyRainOverlay lines 994–1073; SkyWindow `_on_tick` line 823–828; overlay-update-hook callback wiring line 827)
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\overlay.py` (position_changed connections lines 287, 288, 351, 358; `_reanchor_weather` lines 1040–1056; overlay-hook wiring line 348)
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\speech_bubble.py` (set_pose lines 252–270; paintEvent line ~290)
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\dock_mock.py` (lines 29–95)
- `C:\Users\Smabe\tokenpal\tokenpal\ui\qt\_chrome.py` (BuddyResizeGrip lines 104–236)
