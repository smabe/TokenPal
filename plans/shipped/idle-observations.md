# Idle observations — stop riffing on the foreground app while user is AFK

## Goal
When the user has clearly stopped doing anything (no typing, no mouse, no app switches) the buddy should notice the idleness itself instead of repeatedly commenting on whatever app happens to be foregrounded (e.g. Ghostty). Three coordinated changes: continuous idle reading, weight penalty for unchanged app_awareness during low-activity stretches, and an AFK composite line for the LLM context.

## Non-goals
- Changing the idle tier thresholds (2/5/30 min stay as-is).
- Rewriting the cooldown formula. We only extend the ceiling when sustained-idle is active — base formula stays.
- Adding a new sense file. The continuous reading lives inside the existing `idle` sense.
- Rewriting `_pick_topic`. Surgical reweight only.
- Changing how `typing_cadence` emits — only consumed in the new composite.
- Touching the near-duplicate (trigram Jaccard) guard.
- Adding new config keys for idle-related tuning unless a test forces it. Constants in-file are fine for v1.

## Files to touch
- `tokenpal/senses/idle/pynput_idle.py` — replace the steady-state `return None` (line 89-90) with a sustained-idle reading that fires every 60s while idle (tracked via new `_last_sustained_emit` field — do NOT mutate `_idle_start`, return path needs it pristine). Tier-scaled confidence (0.3 short, 0.5 medium, 0.7 long) so a fresh sustained reading can't outrank a return-from-idle transition (return gets 1.5× change_bonus and survives competition). Summary like "User has been idle for 8 minutes" with `data={"event": "sustained", "idle_seconds": …, "tier": …}`.
- `tokenpal/brain/orchestrator.py` — TWO edits:
  1. `_pick_topic` (~line 1282): when `self._context.activity_level() < 0.15` AND the active `idle` reading's `data["event"] == "sustained"` AND the picked sense's `reading.summary == prev_summary`, multiply weight by 0.2. Gating on the explicit sustained-idle signal (not just low activity) keeps quiet typing from getting demoted falsely.
  2. Cooldown formula (~line 761): when sustained-idle is active, raise the cooldown ceiling from 90s to 180s. User explicitly asked the buddy to comment LESS during idle stretches, not just retarget what it talks about — the weight penalty alone won't reduce comment frequency.
- `tokenpal/brain/context.py` — TWO connected edits:
  1. `_detect_composites` (~line 144): change return type from `list[str]` to `list[tuple[str, set[str]]]` (line + senses-to-suppress). Add AFK composite emitting "User is parked on Ghostty — no input for 6 minutes" with `suppressed_senses={"idle"}` when `idle.data.event == "sustained"` AND `app_awareness` summary matches `prev_summary("app_awareness")` AND (typing_cadence absent OR `data["bucket"] == "idle"`). PREPEND AFK to the composite list — there's a hard 2-line cap and AFK is the highest-signal context the LLM gets.
  2. `snapshot()` (~line 56): two-pass build — first call composites, collect suppressed_senses set, then iterate `self._readings` skipping suppressed names. Avoids printing both the raw idle line AND the AFK composite.
- `tests/test_senses/test_idle.py` (NEW) — cover transition→sustained emission cadence, that going active→idle still emits no first reading, and that returning emits the existing return reading exactly once.
- `tests/test_brain/test_context_composites.py` (NEW or extend existing) — AFK composite fires when expected, doesn't fire when app changed within the window, doesn't fire when typing_cadence is `slow`/`normal`/`rapid`/`furious`.
- `tests/test_brain/test_orchestrator.py` (extend) — `_pick_topic` weight penalty kicks in at low activity + unchanged summary, doesn't kick in when summary changed, doesn't kick in at high activity.
- `CLAUDE.md` — one-line update under "Senses" → `idle` describing sustained emission cadence; one-line note under "Brain" / composites describing the AFK composite.

## Failure modes to anticipate
- **Sustained-idle reading floods the topic roulette.** If we emit every poll (1s) the change_bonus stays at 0.5x but the freshness stays at ~1.0 — could inflate idle's weight too high. Mitigation: emit at 60s cadence (every 60th poll) plus on tier-bump (short→medium→long). Verify with the dynamic-cooldown math that the buddy still pauses correctly.
- **Confidence shadowing the return-from-idle one-shot.** The existing transition reading has confidence 0.3–1.0 by tier. If sustained-idle (confidence 0.5 mid-tier) is fresher than a tier-matched return, sustained could win the dice. Mitigation: tier-scaled sustained confidence (0.3 / 0.5 / 0.7) leaves headroom — return-from-idle gets 1.5× change_bonus AND is brand-new, so 0.3×1.5=0.45 beats sustained's 0.3×0.5=0.15 in the same tier. Validated in research pass.
- **Composite 2-line cap squeezes out AFK.** `_detect_composites` returns `[:2]`. If high-CPU + flow-state + late-night all already fire, AFK gets dropped despite being the most relevant signal. Mitigation: prepend AFK to the composite list so it survives the cap.
- **Plan's "comment less when idle" goal isn't met by weight penalty alone.** Demoting app_awareness only retargets which topic wins; cooldown formula `max(30, 90 - activity*60)` still allows comments every 90s when activity≈0. Research-pass finding. Mitigation: cooldown ceiling extension (180s) when sustained-idle active.
- **AFK composite fires while user is reading documentation in the foregrounded app.** Mouse scroll counts as input via `_keyboard_bus` + mouse listener — passive reading that uses scrolling won't trigger idle. Good. But fully passive watching (no mouse) will. That's actually the desired behavior — confirmed by the user's exact complaint.
- **Composite double-counts with the sustained-idle observation line.** snapshot() already prints idle's sustained summary; the composite adds another AFK-themed line. Mitigation: when the AFK composite fires, suppress the raw `idle` summary line in `snapshot()` for that snapshot only — composite supersedes raw. Implement via a small "suppressed sense" set local to one snapshot pass.
- **Topic penalty unintentionally demotes legitimate `productivity` or `time_awareness` repeat readings.** Both senses have stable summaries during true idle (productivity buckets stay flat, hour barely changes). Demoting them is fine because the buddy SHOULD be commenting on idleness, not "still 3pm."
- **`activity_level()` doesn't pull from typing_cadence at all.** It blends app-switch frequency + hardware load. A user typing furiously in one app (no app switches) registers as low activity. Could that mis-fire the penalty? In practice, typing fast pumps hardware CPU on most setups, but a quiet machine + sustained typing could trigger the penalty falsely. Mitigation: also bail out of the penalty if `typing_cadence` reading is `rapid`/`furious` (not just `idle`). Actually simpler: bail out if `idle` sense isn't currently emitting a `sustained` reading. Use that as the explicit "we are AFK" gate.
- **Test isolation for time-based polling.** `pynput_idle.poll` reads `time.monotonic()` directly. New tests need a clock-injection knob OR `monkeypatch` against `time.monotonic`. Match whatever `tests/test_senses/test_typing_cadence.py` does.
- **Lint/type drift.** `_detect_composites` mypy-strict — new dict access needs the same `if active.get(...)` guards as existing branches.

## Done criteria
- [ ] `idle` sense emits a `sustained` reading every 60s while idle, with tiered confidence and a clear summary string.
- [ ] `idle` sense still emits the existing return-from-idle reading exactly once on activity resumption (regression test passes).
- [ ] `_pick_topic` demotes any sense whose summary matches `prev_summary` when `activity_level() < 0.15` AND the `idle` sense is currently in `sustained` state.
- [ ] Cooldown ceiling raised from 90s to 180s when sustained-idle is active so the buddy actually pauses longer between AFK comments.
- [ ] `_detect_composites` returns `list[tuple[str, set[str]]]` with the AFK composite prepended; `snapshot()` two-pass build skips suppressed senses.
- [ ] All new tests pass (`pytest tests/test_senses/test_idle.py tests/test_brain/test_context_composites.py tests/test_brain/test_orchestrator.py`).
- [ ] Existing test suite stays green (`pytest`).
- [ ] `ruff check tokenpal/` clean. `mypy tokenpal/ --ignore-missing-imports` clean.
- [ ] Manual dogfood: open Ghostty, walk away for 10 minutes, confirm chat log shows the buddy commenting on the idleness itself, not "still on Ghostty" twins.
- [ ] CLAUDE.md updated.

## Parking lot
(empty — append "ooh shiny" thoughts here mid-work)
