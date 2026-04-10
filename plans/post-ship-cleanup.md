# Phase 6 Post-Ship Cleanup Plan [DONE]

## Context

Phase 6 (Next Session dashboard card) shipped today in commit `26d8b4f` and was deployed to TestFlight as build 14. The implementation is solid: 553/553 tests, sim-verified for both rest-day and cold-start modes, swiftui-pro review issues all addressed in-PR.

But we left behind three kinds of debris that should be cleaned up before the next session:

1. **Stale plan files.** `.claude/plans/` has 9 Phase 6 files (`phase6-master.md`, 7 persona inputs, and `phase6-ui-approval.md`). Together they're ~110KB of brainstorm artifacts, persona disagreements, "open questions", "file-by-file implementation order", etc. — almost all of it is now historic. Future sessions should be able to find Phase 6 context in 30 seconds, not by paging through a closed brainstorm.

2. **Stale memory.** `project_phase6_suggestion_plan.md` still reads as a mid-flight implementation plan ("Phase 1 shipped uncommitted", "Resume at Phase 3"). The MEMORY.md index points at plan files that we're about to delete.

3. **Phase 6.1 backlog living in commit prose.** The follow-up tightenings (composite z-score, accessory lifts, recency-weighted lookback, scaled gap thresholds, edit-mode placeholder, etc.) currently exist only as a paragraph in the commit body. They need to live in GitHub issues so they survive `git log` archaeology and get prioritized properly.

Goal of this plan: leave a clean slate so the next session starts with no Phase 6 cruft, an accurate memory record, and a tracked backlog.

## Step 1 — Clean up `.claude/plans/`

**Delete** (all Phase 6 brainstorm/spec files, now historic):
- `.claude/plans/phase6-master.md`
- `.claude/plans/phase6-ios.md`
- `.claude/plans/phase6-ios-mini.md`
- `.claude/plans/phase6-ux.md`
- `.claude/plans/phase6-ux-mini.md`
- `.claude/plans/phase6-coach.md`
- `.claude/plans/phase6-data.md`
- `.claude/plans/phase6-qa.md`
- `.claude/plans/phase6-ui-approval.md`

That's 9 files, ~110KB. The shipped code is the spec now — anything we'd ever need from these files is either in the code, in the commit message, or in the GitHub issues we're about to file.

**Keep:** the other plan files (`data-query-*.md`, `strength-insights-*.md`, `workout-tracker-*.md`) — they document features that shipped earlier and are not in scope for cleanup today.

**Keep:** `~/.claude/projects/-Users-smabe-projects-HealthData/plans/issue-31-*.md` and `issue-32-*.md` (they're issue investigation notes, not Phase 6 cruft).

## Step 2 — Update memory

**Edit `~/.claude/projects/-Users-smabe-projects-HealthData/memory/project_phase6_suggestion_plan.md`:**
Rewrite to a tight 10-line "shipped" record. New content:
- Frontmatter: description updated to "SHIPPED 2026-04-07, commit 26d8b4f, TestFlight build 14. Phase 6.1 backlog in GitHub issues."
- Body: one paragraph naming the commit, the build number, what shipped (5 modes, MuscleBalanceLogic bug fix), test count delta (545 → 553), and a pointer line: "Phase 6.1 backlog tracked in GitHub issues — search label `phase-6.1` or look for issues opened 2026-04-07." Plus a 1-line note that the plan files in `.claude/plans/` were deleted in this cleanup.

**Edit `~/.claude/projects/-Users-smabe-projects-HealthData/memory/MEMORY.md`:**
The line for "Phase 6 Suggestion Plan" already says SHIPPED. Just confirm it's accurate post-rewrite. Optionally also touch up `project_feature_todos.md` line if it still implies Phase 6 is pending.

**Optional new memory file** — `feedback_parallel_build_glitch.md`: I hit the same parallel-build glitch ~5 times today where `test_sim` failed because the test target compiled before the main module rebuild was visible; running `build_sim` first and then `test_sim` always cleared it. Worth saving as a `feedback` memory so future sessions don't burn time diagnosing it. Low-stakes addition — skip if you'd rather keep memory lean.

## Step 3 — File Phase 6.1 backlog as GitHub issues

Use `gh issue create` for each. Project labels available: `enhancement`, `bug`, `documentation`, `user-feedback`. None of these are bugs (the maxVolume bug already shipped fixed). Most are `enhancement`; one is `documentation`.

**Issue 1 — `enhancement`: Phase 6.1: smarter Next Session suggestion math (epic)**

Body summary: The Phase 6 Next Session card shipped with intentionally-simple v1 logic. The master plan called for several refinements that were deferred to keep the v1 surface focused. This is the umbrella issue for the math tightening pass. Checklist:
- [ ] Composite z-score `worstGap` (currently days-only desc → ties broken by lower volume). Master plan: `z_days + z_volume_deficit` so a 12-day chest gap with high volume doesn't outrank a 10-day back gap with no volume.
- [ ] Accessory lift in `buildMiniBlock` — currently anchor-only. Master plan: anchor + optional accessory when gap severity warrants. Gate behind a severity threshold to avoid junk volume.
- [ ] Recency-weighted 6-week lookback in `WorkoutCadenceLogic.buildModel` — currently uniform across observed window. Master plan: half-life 14 days so last week counts more than 5 weeks ago.
- [ ] Scale-to-user gap thresholds in `MuscleBalanceLogic.detectGaps` — currently fixed `>= 10 days untrained / < 6 weekly sets`. Master plan: scale to user's median inter-session gap, floor 7d / ceiling 21d.
- [ ] Cold-start gate refinement in `WorkoutCadenceLogic.buildModel` — currently `completed.count < 8`. Master plan: `< 3 weeks AND < 8 sessions AND no weekday with ≥ 3 occurrences`. Three-prong gate that's harder to game.

Files in scope: `Services/ExerciseSuggestionLogic.swift`, `Services/WorkoutCadenceLogic.swift`, `Services/MuscleBalanceLogic.swift`, plus the corresponding test files. TDD strict — each refinement = new failing test first.

**Issue 2 — `enhancement`: Next Session card: edit-mode placeholder so users can re-pin after hiding**

Body: `NextSessionSectionDef.isAvailable(vm:)` currently returns false when the suggestion is nil OR the suggestion is `.activeSession`. Both correct for normal mode, but in edit mode the section disappears entirely — the user can't drag it back from the hidden tray because it never appears in the tray. Master plan calls for `isAvailable` to take an `isEditing` parameter (or a separate `isAvailableInEditMode` method) so the section always shows up as a placeholder in edit mode.

Files: `Services/DashboardSection.swift` (protocol + NextSessionSectionDef), `Sitrep/SitrepView.swift` (call site that filters sections in edit mode).

**Issue 3 — `enhancement`: Refactor: consolidate `recomputeNextSession` call sites in SitrepView**

Body: SitrepView currently calls `vm.recomputeNextSession(store:referenceDate:)` from 4 places (.task initial load, .task post-load, scenePhase becomes active, activeSession id changes). Smell flagged in swiftui-pro review. Should collapse into one VM-owned method that the view triggers in fewer places — possibly via observation of the store directly.

Files: `Sitrep/SitrepView.swift`, `Sitrep/SitrepViewModel.swift`.

**Issue 4 — `enhancement`: Phase 6 polish: locale-aware weekday symbols + minor cleanups**

Body: Bundled small follow-ups from the swiftui-pro review:
- [ ] `CadenceChips` uses two static `[Int: String]` dictionaries for weekday abbreviations. Switch to `Calendar.current.shortWeekdaySymbols` so it's locale-correct (free i18n).
- [ ] `AlreadyLoggedContent.headerLine` does `max(1, Int(duration / 60))` — switch to `Duration.UnitsFormatStyle` for i18n-safe formatting.
- [ ] `LiftRow` hard-codes `3×8`. Will become data-driven when Phase 6.5 prescription engine lands; until then, add a TODO with the issue link rather than leaving it bare.

Files: `Sitrep/Sections/NextSession/CadenceChips.swift`, `AlreadyLoggedContent.swift`, `LiftRow.swift`.

**Issue 5 — `documentation`: CLAUDE.md sub-doc reference still names `WorkoutQuickStartBar`**

Body: Phase 6 deleted `WorkoutQuickStartBar.swift` and replaced its resume branch with `ActiveWorkoutResumeBar.swift`. CLAUDE.md still has a line under the project structure listing the old file (search for "WorkoutQuickStartBar" in `CLAUDE.md`). One-line edit. Tagged `documentation`.

Files: `CLAUDE.md`.

**Issue 6 — `enhancement`: Phase 6.5: prescription engine (sets × reps × target weight)**

Body: Phase 6 master plan called this Option C and deferred it. The strength-coach persona's "centerpiece" recommendation: build per-exercise next-session prescriptions using the existing `ProgressiveOverloadLogic` + `PRDetectionLogic`. Instead of "Chest 11d, Bench Press 3×8" the card would show "Bench Press 3×5 @ 230 lbs (last: 225 × 5)". Bigger scope (~700 LOC + tests). Should be its own brainstorm before any code — file as a parked idea, not actionable. Reference: deleted plan files captured the original persona debate at git revision before commit `26d8b4f`.

Files: TBD (would touch most of `Services/ExerciseSuggestionLogic.swift`, possibly a new `PrescriptionLogic.swift`).

## Step 4 — Verification

After all three steps land:

1. **Plans dir is clean:** `ls .claude/plans/ | grep phase6` returns nothing.
2. **Memory is accurate:** `cat ~/.claude/projects/-Users-smabe-projects-HealthData/memory/project_phase6_suggestion_plan.md` reads as a 10-line shipped record, no resume checklist, no "Phase 1 — what shipped (uncommitted)".
3. **MEMORY.md index** still resolves — every linked file exists.
4. **GitHub issues filed:** `gh issue list --state open --search "phase 6"` shows the new issues. Each has the right label and a clear actionable body.
5. **No broken symlinks** in `~/.claude/projects/-Users-smabe-projects-HealthData/plans/` (the dir referenced in `reference_plans_dir.md` memory).
6. **Working tree clean** at end: `git status` shows only the pre-existing untracked screenshots (`screen_*.png`).

No code changes, no tests to run, no commit needed. This is a paperwork pass.

## Critical files referenced

- `~/.claude/projects/-Users-smabe-projects-HealthData/memory/MEMORY.md` (memory index)
- `~/.claude/projects/-Users-smabe-projects-HealthData/memory/project_phase6_suggestion_plan.md` (Phase 6 record — to rewrite)
- `.claude/plans/phase6-*.md` × 9 (to delete)
- `CLAUDE.md` (only as a reference for issue 5 — not edited in this cleanup)
