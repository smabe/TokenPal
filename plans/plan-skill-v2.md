# /plan Skill v2 — Post-Approval Research Pass

> **Origin** (2026-04-10): During the `pipeline-hardening` session, `/plan` was invoked with a bare slug and the initial plan was drafted from a quick `wc -l` + one grep of a 1184-line file. Mid-work, the implementation assistant had to backtrack after discovering `remote_finetune()` couldn't be cleanly modified without a 180-line re-indent — a shape problem that a research pass would have caught before commit 1 started. User flagged it mid-session; this plan is the follow-through. Research done 2026-04-11 confirmed the skill is pure markdown, the `brainstorm` skill already exists and does most of the work, and agent dispatch via the `Agent` tool is well-established in `graphify`. Scope is much smaller than the seed file suggested.

## Goal
Add a post-approval research-and-brainstorm pass to the `/plan` skill's Mode 1 workflow, so that after the user approves the *initial* plan draft, the skill actively remedies thin context (by dispatching Explore + brainstorm agents) before any code is written, then presents revised findings for a second, narrower approval. This makes "research → plan → code → simplify → test" the default path instead of relying on the assistant to catch misalignment mid-work.

## Non-goals
- **No new skill infrastructure.** Reuse `Agent` tool dispatch (pattern from `graphify/SKILL.md`) and invoke the existing `brainstorm` skill as a black box. No changes to how skills are loaded or executed.
- **No rewrite of Mode 2 (resume) or Mode 3 (ship).** Only Mode 1's post-approval step gets new behavior.
- **No changes to the plan template structure.** The template (Goal / Non-goals / Files to touch / Failure modes / Done criteria / Parking lot) stays identical. Research findings may *refine* the content of existing sections, not add new ones.
- **No auto-research on every plan.** Users must be able to skip the research pass via `--no-research`. Small/obvious plans shouldn't pay the token cost.
- **No change to the user-facing invocation.** `/plan <slug>` still drafts and writes the initial plan file exactly as today. The research pass fires *after* first approval, not before.
- **No change to `feedback_plan_discipline.md`** unless the template grows new sections (it shouldn't).
- **No turning `/plan` into a code skill.** It stays pure markdown. Implementation is editing `SKILL.md`.
- **No recursive invocation.** Research agents must not themselves invoke `/plan` — the skill instructions need to call this out explicitly to prevent infinite loops.
- **No "always brainstorm" mandate.** Brainstorm is one *option* for the research pass alongside Explore agents; the skill picks the right tool for the plan's shape.

## Files to touch
- `~/.claude/skills/plan/SKILL.md` — add a new "Step 4.5: Post-approval research pass" section in Mode 1, describing: (a) when to run it / when to skip via `--no-research`, (b) how to dispatch Explore agents for file-mapping, (c) how to optionally invoke the `brainstorm` skill, (d) how to present revised findings, (e) the re-approval prompt template.
- `plans/plan-skill-v2.md` — this file (once shipped, moves to `plans/shipped/`).
- Possibly `~/.claude/skills/plan/SKILL.md`'s Mode 1 step numbering — the new step slots between current steps 6 (show plan) and 7 (block on response), so downstream steps shift.

## Failure modes
- **Research agents return unhelpful or conflicting output** → skill needs a documented "proceed anyway with a caveat" fallback, not a hard abort.
- **User is annoyed by the extra approval step on small plans** → `--no-research` must be prominent in the skill's quick-start section, and the skill should also suggest skipping when the plan lists ≤3 files to touch.
- **Research eats tokens for plans that don't need it** → heuristic suggestion in the skill: "if the plan's 'Files to touch' section is specific and ≤3 files, consider `--no-research`." Also: the research pass should explicitly NOT re-read files the assistant has already read in the current conversation (idempotent research).
- **Brainstorm persona output conflicts with user's stated preferences** → the skill frames findings as "here's what the research surfaced — you can reject any of this" not "the plan has been updated."
- **`SKILL.md` gets too long and future-me skims it** → keep the new section under ~60 lines. Factor out any heavy prompt boilerplate into concise bullet steps, not prose.
- **Agent dispatch is brittle if the `brainstorm` skill interface changes** → loose coupling: invoke brainstorm via Skill tool name, not by duplicating its logic. If brainstorm breaks, plan-skill-v2 still works (just without the brainstorm leg).
- **Recursive plan invocation** if a research agent thinks it should itself run `/plan` → explicit instruction in the research agent prompt: "Do NOT invoke the plan skill; your job is research only."
- **The research pass inflates context window** when findings are verbose → the skill instructs research agents to report in under N words, and the presentation step summarizes rather than dumping raw agent output.
- **Second-approval fatigue**: user says "yes, yes, yes" without reading → the re-approval prompt should explicitly list *what changed* from the initial plan (new failure modes, renamed files, scope shifts) so there's something concrete to react to.
- **Dogfood misses real problems** because the test is synthetic or the assistant tees up a "plan-friendly" problem → dogfood needs to be a real feature the user brings, not something I construct.

## Done criteria
- `~/.claude/skills/plan/SKILL.md` has a new "Step 4.5: Post-approval research pass" (or equivalent heading) that describes the flow concretely, with Agent tool invocations matching the graphify pattern.
- The new section documents the `--no-research` skip flag and the skill recognizes it in Mode 1 dispatch.
- The new section explicitly instructs dispatched agents NOT to invoke `/plan` themselves (recursion prevention).
- The re-approval prompt template lists what changed from the initial plan (diff-oriented, not full re-presentation).
- The skill stays under ~200 total lines (currently ~107) — new section is bounded.
- **Dogfood test**: user brings a new feature, runs the updated `/plan <slug>`, research pass fires, produces revised findings, asks for second approval, implementation proceeds correctly. Works end-to-end on one real task.
- No regression: existing Mode 2 (resume) and Mode 3 (ship) behavior unchanged. Verified by a manual re-read of the two sections.
- Plan file shipped to `plans/shipped/plan-skill-v2.md`.

## Parking lot
(empty at start — append "ooh shiny" thoughts that surface mid-work for later)
