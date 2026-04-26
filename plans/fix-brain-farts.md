# Fix brain farts

## Goal
Stop the buddy from replying with offline-style "I lost my train of thought" quips during live conversation. The fine-tuned voice model (75% observation training) reflexively narrates the screen instead of responding to user turns; near-duplicate suppression catches the template-locked reply and falls through to the LLM-offline quip pool, making a working brain look broken. Fix the policy side (retry once with stripped observation context, isolated conv-only suppression window) and the prompt side (re-label the context block, add explicit "respond mode" rule for the `is_finetuned` branch).

## Non-goals
- Retraining or fine-tuning the voice model. Fix is on the prompt and policy side.
- Reworking observation/comment generation pipelines. Conversation path only.
- Touching `_is_near_duplicate` or `_has_recent_prefix_lock` for non-conversation callers (idle-tool rolls, freeform, rage, drift, git-nudge). Their suppression policy stays as-is.
- Adding new toggles to `[conversation]` config unless research says we have to.
- Net-new metric/telemetry surface. We can log differently, but no new dashboards.
- Net-new ack/quip pool. Decided against — retry is the path; if retry also trips, emit anyway.
- Touching `tokenpal/tools/voice_profile.py` or `tokenpal/tools/train_voice.py`. Earlier draft scoped these in for an ack pool that's no longer happening.

## Files to touch
- `tokenpal/brain/orchestrator.py` — `_handle_user_input` (lines 2615-2725): replace the `near-duplicate → get_confused_quip()` branch with a single retry attempt that strips observation context (keeps conversation history) and prepends a "vary your wording" instruction. If the retry also trips near-dup, emit it anyway (suppression bypass on retry). Also: add a `_conversation_recent_outputs` deque (smaller maxlen, conv-only) and switch the conv suppression check to read from it, while still pushing each conv reply into the shared `_recent_outputs` so observations stay aware of what was said.
- `tokenpal/brain/personality.py` — `build_context_injection`: re-label the context block from `"What you currently see on their screen:"` to something like `"Background context (do NOT narrate this; for awareness only):"`. `build_conversation_system_message` (`is_finetuned` branch, line 1308): add an explicit "you are in RESPONSE mode, do not narrate the background context, talk to the user" rule.
- `tests/test_brain/test_conversation.py` — line 358-373 (`test_reply_near_duplicate_suppressed`): UPDATE to reflect new behavior — the existing assertion that suppression returns a `quip` has to flip. Then add a new regression test: `_MockLLM` returns a previously-seen observation-template string, assert (a) retry was triggered, (b) user-visible output is NOT in `_CONFUSED_QUIPS` / `_voice_offline_quips`, (c) on retry-also-trips path, the retry's reply is emitted anyway.

## Failure modes to anticipate
- **Retry burns another inference** and may still trip near-duplicate (template lock is sticky at the embedding level — model was trained 75% observation). Final policy: emit the retry reply anyway. No infinite loop, no stacking quips.
- **Voice-tuned training shape is immovable** — 75% observation / 15% conversation / 10% freeform per `dataset_prep.py:224-226`. Prompt tweaks add guardrails but won't fully break template lock. Policy fallback (retry → emit) is the load-bearing fix; prompt is supporting work.
- **The `_recent_outputs` deque mixes 9 sources** (observation, drift, git-nudge, rage, freeform, EOD, idle-tool check-only, buddy-reaction, conv). A successful observation 2 minutes ago can suppress a fresh conversation reply. Fix: separate `_conversation_recent_outputs` window for the conv suppression check; keep pushing conv replies into shared deque so observations remain aware.
- **Existing `test_reply_near_duplicate_suppressed`** asserts current broken behavior. It has to flip, not just be supplemented. Easy to miss.
- **Idle-tool path is asymmetric**: `orchestrator.py:1524` checks near-dup but doesn't push back into `_recent_outputs`. The fix shouldn't accidentally normalize this.
- **Test layer**: the conv path is heavy on async + side effects (TTS, UI callback, conversation session state). Existing `_MockLLM` + `_make_brain` harness handles this — extend it, don't replace.
- **Stripping observation context on retry**: keeps conv history (model still sees what user/buddy said). Risk: model loses "user is on Ghostty" awareness for that one retry — acceptable, the retry is a fallback path, not a coherence-critical turn.
- **Prompt-side context relabel**: the same string is used (or could be) by other callers — verify `build_context_injection` is conv-only before changing its prefix. If it's shared, fork it cleanly.
- **iMessage / chat-window UX expects every turn to produce something** — retry-then-emit-anyway always emits; no silent drops.
- **Logging**: today we log `(reply suppressed near-duplicate)`. After the fix, we want distinct log lines for `(reply retry-attempted)` and `(reply retry-also-near-dup, emitting anyway)` so dogfood traces remain debuggable.

## Done criteria
- Near-duplicate-suppressed conv replies no longer fall to `_CONFUSED_QUIPS` / `_voice_offline_quips`. Either the retry escapes the lock, or the retry's reply is emitted anyway.
- `test_reply_near_duplicate_suppressed` updated; new regression test covers retry-success and retry-also-near-dup paths. Both green.
- Replaying the 11:07–11:11 scenario (`_MockLLM` returns the "Man, look at you—chillin' like a boss in <APP>!" template) produces a non-confused user-visible output.
- Existing observation / freeform / rage / git-nudge / idle-tool suppression behavior unchanged. Existing tests still green.
- Conv suppression check reads only from the new `_conversation_recent_outputs` window. The shared `_recent_outputs` is still appended to from the conv path so observations stay aware.
- `build_context_injection` re-labeled. `build_conversation_system_message` (`is_finetuned` branch) gains the explicit response-mode rule.
- `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.

## Phases

- **Phase 1 — Load-test first.** Update `test_reply_near_duplicate_suppressed` and add the new regression test, both written to assert the post-fix behavior. Run them — both should FAIL against the current orchestrator. If they don't fail, the test isn't real. This gates further work.
- **Phase 2 — Retry path.** Modify `_handle_user_input`'s near-dup branch (`orchestrator.py:2691-2697`) to retry once with observation context stripped (conv history preserved) and a "vary your wording" prepend. On retry-also-near-dup, emit the retry reply anyway. Phase 1 tests should now pass.
- **Phase 3 — Isolated conv window.** Add `_conversation_recent_outputs` deque (smaller maxlen, conv-only) in `Brain.__init__`. Switch the conv suppression check to read from it. Push conv replies to BOTH the new window and the shared `_recent_outputs` (so observation paths stay aware). Verify non-conv suppression callers untouched.
- **Phase 4 — Prompt-side touch-ups.** Re-label `build_context_injection`'s prefix. Add the response-mode rule to the `is_finetuned` conv system message. Sanity-check no other caller depends on the old prefix string.

Each phase ends with `/simplify` (where the diff isn't trivial), full `pytest`, `ruff`, `mypy`, then a commit before advancing.

## Parking lot
(empty)
