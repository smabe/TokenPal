# clamp-o: rescue the buddy when he slides off-screen

## Goal
When the buddy's COM ends up outside every connected screen's available
geometry after the user releases a fling, wait a couple of seconds, then
gracefully tween him back to the nearest in-bounds point. Live drag and
post-release swing physics stay untouched.

## Non-goals
- No clamp during an active grab. The swing must remain unbounded so a
  hard fling still feels like a fling.
- No clamp during the immediate post-release coast. Going briefly off
  the edge and bouncing back via momentum is fine; only a sustained
  off-screen state triggers rescue.
- No new linear home spring on the simulator (memory:
  `project_qt_physics_handover.md` calls "no linear home spring"
  load-bearing). Rescue is a UI-layer tween, not a physics change.
- No changes to edge-dock snap behavior. That fires on near-edge release;
  rescue handles the far-off-screen case it doesn't cover.
- No multi-monitor layout cleverness. "Nearest available geometry" is
  enough; we don't try to pick the screen the user "meant".

## Files to touch
- `tokenpal/ui/qt/buddy_window.py` — add an off-screen watchdog in
  `_on_tick` (or a sibling timer) that arms when the buddy is outside
  every screen's `availableGeometry()` AND not dragging, fires after
  ~2s, and runs a short tween back to the nearest in-bounds anchor.
  Reuse the `QGuiApplication.screenAt` / `availableGeometry()` pattern
  already in `_maybe_edge_dock`.
- `tests/test_qt_physics.py` (or a new `tests/test_buddy_rescue.py` if
  the helper lives in a new pure-Python module) — cover: (1) the
  geometry helper that decides "outside all screens" + nearest
  in-bounds point, (2) the rescue tween advancing toward target and
  terminating on arrival.

## Failure modes to anticipate
- Tween fights the home spring / angular damping. Mitigation: drive
  rescue via `snap_home` + direct position writes (the simulator already
  exposes `snap_home` for the edge-dock teleport case); have the tween
  zero linear velocity on each step so residual fling momentum doesn't
  shove the body back off.
- Re-arming forever: tween lands on a boundary pixel that itself reads
  as "outside" due to >= vs > comparisons, watchdog re-fires, infinite
  loop. Mitigation: target a point a few pixels INSIDE the rect, and
  suppress the watchdog while a tween is active.
- Multi-monitor seams: COM at x=screen-A.right()+1 reports outside A,
  but is inside B. Mitigation: iterate every `QGuiApplication.screens()`
  and only treat as off-screen when no rect contains the point.
- Dragging during a tween: user grabs mid-rescue. Mitigation: cancel
  the tween on `mousePressEvent` / `_begin_drag`, let physics take over.
- Hidden / minimized buddy still ticking the watchdog and snapping
  while invisible. Mitigation: gate watchdog on `isVisible()`.
- Tween cadence: relying on `_on_tick` means if the body has gone to
  sleep (`_sleep_timer`), nothing ticks. Off-screen sleeping bodies need
  the watchdog to keep the timer awake until either they're back in
  bounds or the user grabs them.
- Coordinate frames: `self._sim.position` is in global screen coords
  (same frame as `availableGeometry`). Confirm before computing
  distances; getting this wrong means rescuing to nonsense.

## Done criteria
- Sliding the buddy fully off any screen and releasing causes him to
  drift back on screen on his own within ~2-3s, and end visible and
  upright.
- An on-screen release with momentum that briefly carries the COM past
  the edge still lets the existing physics + edge-dock handle it; the
  rescue does NOT fire if he's back in bounds before the watchdog
  threshold elapses.
- Grabbing during a rescue cancels the rescue cleanly (no snap-back
  fight, no double-tween).
- New tests pass; existing `tests/test_qt_physics.py` still green.
- Manual run on macOS verifies the slide-off-screen scenario the user
  hit; observed in-window before declaring done (CLAUDE.md UI/Qt rule).

## Parking lot
(empty)
