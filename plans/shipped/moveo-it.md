# moveo-it (idle paint cascade throttle)

## Goal
Cut the always-on iGPU cost (Windows DWM ~19% + Python ~4% on AMD 780M when buddy is idle) by gating the `position_changed` paint cascade on actual motion state. When the buddy is sleeping (and not dragged / not rescuing), nothing should dirty any translucent always-on-top widget. Wake instantly on user input or impulse so flings stay smooth.

## Non-goals
- Not killing the QtQuick transparent-window vsync presents themselves — that requires window-flag surgery and we'll see how much DWM drops once nothing dirties.
- Not changing the 240 Hz fixed physics step.
- Not touching the WeatherSim or sky panel — sky pump is already disabled in this branch as a diagnostic; revert that change in phase 0.
- Not killing the Kokoro TTS worker (agent 3 hypothesis — speculative, gated behind `[audio]` toggles that default off).
- Not adding a config knob for "max FPS".
- Not migrating off the Quick backend.

## Files to touch
- `tokenpal/ui/buddy_core.py` — gate `position_changed.emit()` on `(not sim.sleeping) OR _drag_active OR rescue_pending`. Add an `awake_changed = Signal(bool)` that fires only on true transitions, so backends can subscribe to start/stop their own loops.
- `tokenpal/ui/quick/buddy_window.py` — subscribe to `core.awake_changed`. On `False`: disconnect `frameSwapped` from `_on_sync_tick`, stop the kick timer (re-enable it from the diagnostic — phase 0). On `True`: reconnect frameSwapped, start kick. Also wire the per-screen click-through pollers to slow to ~10 Hz when asleep, 60 Hz when awake.
- `tokenpal/ui/qt/overlay.py` — `_reanchor_weather` (line 1111): gate `_buddy_rain_overlay.update()` on `weather_sim.has_visible_particles()` (new method) OR buddy moved ≥1 px since last call. `_reposition_grip` and `_reposition_dock` chains: per-slot guard so a no-op pose change doesn't trigger `repaint()`.
- `tokenpal/ui/qt/_chrome.py` — `BuddyResizeGrip.set_pose` line ~159: change synchronous `repaint()` to deferred `update()` AND early-return when geometry is unchanged.
- `tokenpal/ui/qt/dock_mock.py` — `set_pose`: early-return when geometry unchanged (mirrors the `_chrome` fix).
- `tokenpal/ui/qt/weather.py` — add `WeatherSim.has_visible_particles()` returning True if any particle list is non-empty OR lightning strobe is in the active window. Used by overlay's gate.
- `tokenpal/ui/quick/_clickthrough.py` — accept a "rate" parameter, expose `set_rate(idle: bool)` so the buddy_window can throttle the per-screen polls.
- Revert the two diagnostic comments from this session: `tokenpal/ui/qt/overlay.py:549` (`# self._sky_window.start()` → restored) and `tokenpal/ui/quick/buddy_window.py:153` (`# self._kick_timer.start()` → restored, but managed by awake_changed).
- Tests: extend `tests/test_qt_overlay.py` or add `tests/test_idle_cascade.py` — assert `position_changed` does NOT emit when sim is sleeping; assert `_buddy_rain_overlay.update()` is not called from `_reanchor_weather` when sim has no particles AND buddy hasn't moved.

## Failure modes to anticipate
- **Sleep oscillation under home spring**: once sim sleeps, anything that nudges sim out of sleep restarts the cascade. The settle thresholds in `physics.py:RigidBodyConfig` already cover this, but verify with a long idle soak (2+ minutes) that the buddy stays asleep and doesn't ping-pong sleep/wake on float drift.
- **First-paint latency on wake**: when `awake_changed(True)` fires, the Quick path needs to reconnect frameSwapped AND get the first present. If we miss the first frame, mouse-press → buddy-paint can lag a vsync. Mitigation: call `_buddy_item.update()` inline on wake transition.
- **Click-through poll too slow at 10 Hz**: cursor crosses the buddy faster than 10 Hz can detect → first 100 ms after a click might mis-route input. Mitigation: keep poll fast (60 Hz) whenever cursor is inside any of the buddy's screen-windows; only slow it when cursor is far away. Or simpler: keep at 60 Hz always — it's CPU-cheap, and the wake-up feels instant.
- **Multi-monitor `_active` window switch during sleep**: user drags a window under another screen — without frameSwapped firing, `_pick_screen` never re-runs and `_active` stays stale. But while asleep the buddy isn't moving, so this can't happen unless something *else* moves the buddy. Should be fine; verify.
- **`position_changed` consumers expect every-tick emits** (e.g. `_reposition_bubble` at overlay.py:337). When the buddy goes to sleep mid-bubble-display, the bubble's anchor freezes — that's actually correct behavior since the buddy isn't moving. But verify the bubble doesn't have its own assumption about getting a heartbeat.
- **Diagnostic-comment revert order**: I left `# self._sky_window.start()` and `# self._kick_timer.start()` commented in this session. Phase 0 reverts both before any other work, so the baseline matches the production state we're throttling.
- **`awake_changed` signal lifecycle**: BuddyCore is a QObject parented under the BuddyWindow / QuickBuddyWindow. Connections need to survive a `_switch_active` (multi-monitor) — they should because they're on the core, not the active window. But verify.
- **Tests using the existing `wake_tick_timer` / `sleep_tick_timer` API may regress** if I change emit semantics. Run the full suite (`pytest tests/`) before commit per phase.
- **Phase 1 falsifiable test**: launch buddy, wait 5 s for settle, then check that `position_changed` is NOT being emitted (instrument with a counter or check Task Manager DWM%). If still high, the cascade gate didn't bite — research was incomplete and I need to look elsewhere (most likely Kokoro TTS worker as agent 3 suggested, or the transparent-window vsync present itself accounting for more than I think).

## Done criteria
- Phase 0: diagnostic comments reverted; baseline restored; `pytest tests/` green.
- Phase 1: `BuddyCore._on_tick` skips `position_changed.emit()` when `sim.sleeping AND not _drag_active AND not rescue_pending`. Phase-1 falsifiable test in place: a unit test that drives a buddy to sleep and asserts emit count stops growing. **This test gates further work** — if it lands red, research was insufficient.
- Phase 2: `awake_changed` Signal added to BuddyCore, wired in Quick path to disconnect/reconnect `frameSwapped`. Idle-soak smoke: leave buddy alone for 30 s, observe Task Manager DWM% drop noticeably from current ~19%.
- Phase 3: Per-slot guards in `_reanchor_weather`, `_reposition_grip`, `_reposition_dock` — each only triggers a paint when its geometry/state actually changed. `BuddyResizeGrip.set_pose` uses deferred `update()`, not synchronous `repaint()`.
- Phase 4: Click-through poll either kept at 60 Hz universally OR throttled per buddy-awake state (decide based on phase 2-3 measurements; if DWM is already in single digits, skip).
- Manual: drag the buddy, fling him, release — motion is smooth (no juddering, no first-paint lag). After release + settle, GPU% drops back to baseline within ~250 ms.
- `pytest tests/` green; `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` green for changed files.

## Parking lot
- QtQuick transparent-window vsync presents (layer 1 of the diagnosis) — if DWM stays high after this plan ships, investigate `setColor(transparent)` alternatives or selective `setVisible(False)` on idle screens.
- Kokoro TTS subprocess GPU residency (agent 3 hypothesis) — verify with TTS toggled off whether Python's 4% drops further.
- `[ui] weather` is a non-flag — `weather = false` in config.default.toml is `[senses] weather`, the visual sky widget is unconditional. Either gate the sky widget on a real config flag or document that the toggle exists but only controls the sense.
- Click-through 60 Hz polls keep the event loop hot but are CPU; if CPU pressure shows up later, throttle.
