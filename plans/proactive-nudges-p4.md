# proactive-nudges-p4 — nudge emission funnel (canned text only)

You are phase `p4` of the `proactive-nudges` plan. This phase delivers, as one commit, a nudge emission funnel that shares the buddy's output guards without inheriting the ambient rate cap — and, in doing so, makes reminders audible for the first time. **The text is still the canned label; no LLM call is added here.** That is p5. p1-p3 shipped the schedule, the scheduler and the tool.

## Locked decisions
See the master `plans/proactive-nudges.md`. Binding here:
- **Own funnel, sharing the guards** (operator decision 5). A nudge gets `filter_response` and TTS; it is **exempt from the ambient rate cap** and the forced-silence breather.
- **Nudges do not dedupe** (decision 5 as refined after the audit, in the master). `_recent_outputs` is one shared deque (`orchestrator.py:399`), so an unrelated ambient comment could suppress a reminder, and two fires of the same reminder trip `_NEAR_DUPLICATE_JACCARD = 0.70` (`:187`, `_is_near_duplicate` at `:1265`) — silencing every fire after the first. A recurring reminder repeating itself is the feature. The funnel neither consults `_is_near_duplicate` nor appends to `_recent_outputs`.
- **The canned label bypasses `filter_response`.** It is the user's own words, not model output, and `filter_response` drops anything under 15 characters (`personality.py:1132`, and again at `:1155`) — so a reminder armed as `"stand up"` would be silently swallowed on every fire. Only generated text is filtered, which matters from p5; wiring it correctly now costs nothing.

## Work
- Scope trace: DIRECT — decision 5. SAFETY for p5: the funnel is where generated text gets its drift guard, so it must exist and be tested before anything generates.
- `tokenpal/brain/orchestrator.py` — a nudge emission path, proposed `_emit_nudge(text: str, *, generated: bool) -> None`. `_emit_comment` (`:1157-1174`, measured at `de1eb32`) has six steps; state which this reuses, in this order:
  1. **`personality.record_comment(text)` — NO.** It feeds catchphrase rotation and running-bit state (`personality.py:738`), which is ambient-voice bookkeeping; a reminder is not the buddy choosing to speak.
  2. `self._ui_callback(text)` — **yes**.
  3. `if getattr(self, "_audio_pipeline", None) is not None: self._speak_async(text, source="ambient")` — **yes, and keep the guard**: `_speak_async` (`:1176`) opens with an `assert` on the pipeline, so an unguarded call crashes every audio-disabled run. This is the line that makes nudges audible; they are silent today because `:1168` is its only ambient call site.
  4. **`self._context.acknowledge()` — NO.** The nudge consumed no observation.
  5. **`_last_comment_time` / `_consecutive_comments` / `_suppressed_streak` — NO.** Note `_suppressed_streak` in particular: `_emit_comment:1173` resets it, and it is what *sets* `_forced_silence_until` (`:1251`), so touching it would indirectly delay forced silence.
  6. **`_comment_timestamps.append(...)` — NO.** That is the rate cap.
  Plus one step `_emit_comment` does not have: when `generated` is True, run `personality.filter_response` first and fall back to the caller's canned label if it returns `None` (it returns `str | None`, `personality.py:1116`; every drop path returns `None`). When `generated` is False, emit as-is.
  Wire `ProactiveScheduler`'s `ui_callback` to `_emit_nudge(..., generated=False)` so every fired nudge goes through the funnel from this phase on. **SUPERSEDED BY p5 (operator, 2026-09-05):** making the funnel the scheduler's callback meant `tick()` emitted inline, which collided with p5 spawning generation from `tick()`'s return and delivering through the same funnel — two bubbles per fire. p5 removes `ui_callback` from the scheduler entirely and moves delivery to `Brain._fire_due_nudges`. See p5's Decisions.
  The scheduler's `ui_callback` is wired at `orchestrator.py:425`.
- `tokenpal/brain/proactive.py` — **added at execution.** The rewiring falsified two statements in this file: the `ui_callback` parameter doc ("Must behave like `brain._ui_callback` (post speech bubble)") and the module docstring's first Design rule, which promised bubble-only delivery. Planning miss: a rewiring falsifies the docs of the thing being rewired.
- `tests/test_brain/test_nudge_emission.py` — new:
  1. a fired nudge calls `_speak_async` when `_audio_pipeline` is set, and does not raise when it is `None` — the gap this phase closes.
  2. a nudge fires while `_forced_silence_until` is in the future and `_should_comment` would refuse → **it still fires** (the exemption, and the point of decision 5).
  3. after a nudge, `_last_comment_time`, `_comment_timestamps`, `_consecutive_comments` and `_suppressed_streak` are all unchanged, and `_recent_outputs` has not grown.
  4. the same reminder firing twice with identical text ships **both** times — the anti-dedupe rule, which is the operator's promise guarantee.
  5. an 8-character label (`"stand up"`) fires and is emitted verbatim, not swallowed by the 15-character minimum.

## Decisions & findings
### Decision: a second funnel rather than a flag on `_emit_comment`  *(status: active; operator-chosen 2026-09-04)*
- **Rationale:** operator chose "own funnel, sharing the guards" over routing through `_emit_comment`, because a reminder the user explicitly armed must not be suppressed by chattiness rules written for ambient commentary. `_emit_comment` is where the rate accounting lives, so the exemption is most of the function.
- **Alternatives considered:** an `exempt_from_cap` parameter on `_emit_comment` — rejected as a flag that changes what half the function does. **`GatePolicy.BYPASS_CAP`** (`wedge.py:21`, used by `rage.py:21`, `git_nudge.py:21`) is the repo's existing exemption idiom and was considered: it skips `_should_comment` (`:1047`) but still routes through `_emit_comment`, which *does* record into `_comment_timestamps`/`_last_comment_time` (`:1171-1174`). That is the weak reading; this plan takes the strong one. **Consequence accepted:** a nudge no longer counts as "the buddy spoke", so an ambient comment can land immediately behind one. **`_riff`** already chains prompt → LLM → `filter_response` → dedupe → `_emit_comment` and was considered as the host: rejected because it is awaited inline (violating decision 6) and because a Wedge emits one candidate per tick while the scheduler is multi-tenant.

### Findings from execution  *(2026-09-05)*
- **The proposed signature could not express the behaviour this phase mandates.** Work proposed `_emit_nudge(text: str, *, generated: bool)` and then required a filtered-out generation to "fall back to the caller's canned label" — but with one string parameter there is no label to fall back to. Both readings are wrong: falling back to the unfiltered `text` re-emits exactly the drifted output the filter just rejected, defeating this phase's own SAFETY clause; emitting nothing violates "treat `None` as fallback, never as 'say nothing'". Shipped as `_emit_nudge(label: str, *, generated: str | None = None)` — same arity, ownership, mutation set and error path, with the boolean becoming the payload it was gating. It also makes `_emit_nudge(generated_text, generated=False)` unrepresentable, which is the bug class the boolean invited, and it is directly assignable to `ui_callback: Callable[[str], None]` with no lambda. **p5's call site is `self._emit_nudge(label, generated=text)`** where `text` is the model's output or `None`.
- **The phase's ONE production change was untested and the whole suite stayed green without it.** Every test built its own `ProactiveScheduler`, so reverting `ui_callback=self._emit_nudge` to `self._ui_callback` — a plausible rebase loss, since that line is the entire non-test delta — passed 2446 tests while every nudge silently lost its TTS. `test_brain_wires_the_scheduler_to_the_nudge_funnel` constructs a real `Brain` and pins the callback identity. Mutation-verified.
- **`tick()` swallows every `ui_callback` exception** (`proactive.py:229-234`), so a test that fires through `tick()` can never observe a raise — the audio-disabled test asserted an empty `spoken` list and would have passed with the guard removed *and* the real `_speak_async` restored, logging a traceback per fire. That test now calls `_emit_nudge` directly.
- **The ambient-TTS guard now has two consumers, so it was extracted** to `_speak_ambient`. Note the extraction also covers `_emit_comment`, which is why that method's diff is larger than the funnel alone would justify.
- **`filter_response` cannot return `""`.** Every path returns `None` or text that already passed a `len(text) < 15` check applied twice (`personality.py:1132`, `:1155`), so `filter_response(generated) or label` is equivalent to an `is None` test. Verified, not assumed.
- **A nudge cannot speak into an open mic.** `_proactive_paused` returns True on `_in_conversation`, and voice input creates a `ConversationSession`, so the voice FSM's listening window is always inside an active conversation. No new exposure over ambient's.
- **Skipping `record_comment` has exactly one consequence and it is desirable.** It maintains `_recent_comments` (fed into the "avoid repeating these" prompt block and `_is_catchphrase_echo`), `_total_comments` and `_consecutive_snarky`. Nothing else reads state only it writes. Skipping it is what lets a nudge repeat verbatim without poisoning the next ambient prompt.

## Failure modes to anticipate
- **A nudge bubble stomps an in-flight bubble.** There is no queue at the UI layer: `show_text` replaces the text and restarts the auto-hide timer, which is sized for TTS pace (`ui/qt/overlay.py:1180,1189-1191`). Not introduced here and not fixed here.
- The Qt overlay's confirm dialog is `show()`-based, so the loop keeps pumping and a nudge can render over an open modal. Parking lot — do not fix, and do not let a test depend on the current behaviour.
- `filter_response` signals silence with `None`, not `""`. Treat `None` as fallback, never as "say nothing" — the user armed this.
- Do not route the canned label through `filter_response` "for consistency". The 15-character minimum applies twice (`personality.py:1132` and `:1155`) and would silently break short labels, which are the natural ones.

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
