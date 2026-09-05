# proactive-nudges-p4 — nudge emission funnel (canned text only)

You are phase `p4` of the `proactive-nudges` plan. This phase delivers, as one commit, a nudge emission funnel that shares the buddy's output guards without inheriting the ambient rate cap — and, in doing so, makes reminders audible for the first time. **The text is still the canned label; no LLM call is added here.** That is p5. p1-p3 shipped the schedule, the scheduler and the tool.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **Own funnel, sharing the guards** (operator decision 5). A nudge gets `filter_response` and TTS; it is **exempt from the ambient rate cap** and the forced-silence breather.
- **Nudges do not dedupe** (decision 5 as refined after the audit, in the master). `_recent_outputs` is one shared deque (`orchestrator.py:195,394`), so an unrelated ambient comment could suppress a reminder, and two fires of the same reminder trip `_NEAR_DUPLICATE_JACCARD = 0.70` (`:187`) — silencing every fire after the first. A recurring reminder repeating itself is the feature. The funnel neither consults `_is_near_duplicate` nor appends to `_recent_outputs`.
- **The canned label bypasses `filter_response`.** It is the user's own words, not model output, and `filter_response` drops anything under 15 characters (`personality.py:1132`) — so a reminder armed as `"stand up"` would be silently swallowed on every fire. Only generated text is filtered, which matters from p5; wiring it correctly now costs nothing.

## Work
- Scope trace: DIRECT — decision 5. SAFETY for p5: the funnel is where generated text gets its drift guard, so it must exist and be tested before anything generates.
- `tokenpal/brain/orchestrator.py` — a nudge emission path, proposed `_emit_nudge(text: str, *, generated: bool) -> None`. `_emit_comment` (`:1136-1153`) has six steps; state which this reuses, in this order:
  1. **`personality.record_comment(text)` — NO.** It feeds catchphrase rotation and running-bit state (`personality.py:738`), which is ambient-voice bookkeeping; a reminder is not the buddy choosing to speak.
  2. `self._ui_callback(text)` — **yes**.
  3. `if getattr(self, "_audio_pipeline", None) is not None: self._speak_async(text, source="ambient")` — **yes, and keep the guard**: `_speak_async` opens with `assert self._audio_pipeline is not None` (`:1157`), so an unguarded call crashes every audio-disabled run. This is the line that makes nudges audible; they are silent today because `:1147` is its only ambient call site.
  4. **`self._context.acknowledge()` — NO.** The nudge consumed no observation.
  5. **`_last_comment_time` / `_consecutive_comments` / `_suppressed_streak` — NO.** Note `_suppressed_streak` in particular: `_emit_comment:1152` resets it, and it is what *sets* `_forced_silence_until` (`:1223-1228`), so touching it would indirectly delay forced silence.
  6. **`_comment_timestamps.append(...)` — NO.** That is the rate cap.
  Plus one step `_emit_comment` does not have: when `generated` is True, run `personality.filter_response` first and fall back to the caller's canned label if it returns `None` (it returns `str | None`, `personality.py:1116`; every drop path returns `None`). When `generated` is False, emit as-is.
  Wire `ProactiveScheduler`'s `ui_callback` to `_emit_nudge(..., generated=False)` so every fired nudge goes through the funnel from this phase on.
- `tests/test_brain/test_nudge_emission.py` — new:
  1. a fired nudge calls `_speak_async` when `_audio_pipeline` is set, and does not raise when it is `None` — the gap this phase closes.
  2. a nudge fires while `_forced_silence_until` is in the future and `_should_comment` would refuse → **it still fires** (the exemption, and the point of decision 5).
  3. after a nudge, `_last_comment_time`, `_comment_timestamps`, `_consecutive_comments` and `_suppressed_streak` are all unchanged, and `_recent_outputs` has not grown.
  4. the same reminder firing twice with identical text ships **both** times — the anti-dedupe rule, which is the operator's promise guarantee.
  5. an 8-character label (`"stand up"`) fires and is emitted verbatim, not swallowed by the 15-character minimum.

## Decisions & findings
### Decision: a second funnel rather than a flag on `_emit_comment`  *(status: active; operator-chosen 2026-09-04)*
- **Rationale:** operator chose "own funnel, sharing the guards" over routing through `_emit_comment`, because a reminder the user explicitly armed must not be suppressed by chattiness rules written for ambient commentary. `_emit_comment` is where the rate accounting lives, so the exemption is most of the function.
- **Alternatives considered:** an `exempt_from_cap` parameter on `_emit_comment` — rejected as a flag that changes what half the function does. **`GatePolicy.BYPASS_CAP`** (`wedge.py:21`, used by `rage.py:21`, `git_nudge.py:21`) is the repo's existing exemption idiom and was considered: it skips `_should_comment` at `orchestrator.py:1353` but still routes through `_emit_comment`, which *does* record into `_comment_timestamps`/`_last_comment_time` (`:1150-1153`). That is the weak reading; this plan takes the strong one. **Consequence accepted:** a nudge no longer counts as "the buddy spoke", so an ambient comment can land immediately behind one. **`_riff`** (`:1365-1410`) already chains prompt → LLM → `filter_response` → dedupe → `_emit_comment` and was considered as the host: rejected because it is awaited inline (violating decision 6) and because a Wedge emits one candidate per tick while the scheduler is multi-tenant.

## Failure modes to anticipate
- **A nudge bubble stomps an in-flight bubble.** There is no queue at the UI layer: `show_text` replaces the text and restarts the auto-hide timer, which is sized for TTS pace (`ui/qt/overlay.py:1180,1189-1191`). Not introduced here and not fixed here.
- The Qt overlay's confirm dialog is `show()`-based, so the loop keeps pumping and a nudge can render over an open modal. Parking lot — do not fix, and do not let a test depend on the current behaviour.
- `filter_response` signals silence with `None`, not `""`. Treat `None` as fallback, never as "say nothing" — the user armed this.
- Do not route the canned label through `filter_response` "for consistency". The 15-character minimum applies twice (`personality.py:1132-1135`) and would silently break short labels, which are the natural ones.

## Done criteria
- On this Mac with `[audio] speak_ambient_enabled = true`: a fired nudge is **spoken aloud**. Today it is not — this is the observable the phase exists for, and it needs the operator or a live run to confirm.
- A nudge fires during a forced-silence window that would suppress an ambient comment.
- Firing the same reminder twice emits twice; `_recent_outputs` is unchanged by either.
- An 8-character label is emitted verbatim.
- `grep -n "_speak_async" tokenpal/brain/orchestrator.py` shows three call sites: ambient, typed, and the nudge funnel.
- `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.


## Carried in from p2  *(2026-09-05, do not rediscover)*
- **The funnel must not tick the scheduler from anywhere the brain loop does not already.** `MemoryStore.teardown()` sets `_conn = None` while `enabled` stays `True`. `mark_reminder_fired` now returns `True` on a closed store precisely so an outage cannot disarm reminders, but a funnel that ticks after memory teardown would still write fires nowhere. The brain loop stops before `memory.teardown()` (`app.py:1839-1844`); keep it that way.
- **`tick()` already spaces deliveries by `_MIN_NUDGE_GAP_S` (16 s)** because the bubble replaces rather than queues and lingers 15 s (`ui/qt/overlay.py:75`). If the funnel adds its own pacing, do not double-gate — and if the funnel introduces real queueing, the scheduler-level gap is what should then be reconsidered, not duplicated.
- **`tick()` returns the live `ScheduledNudge` objects held in `_nudges`.** `register()` replaces the dict entry with a new object and `cancel()` pops it, so a generation task holding a returned reference can outlive the reminder it describes and deliver text for something the user just cancelled. Pass the id, or a copy, into anything asynchronous.
