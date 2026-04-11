# /plan Skill v2 (seed, NOT an approved plan)

> ⚠️ **This is a seed file, not an executable plan.** It captures a thought from the 2026-04-10 `pipeline-hardening` session so it doesn't get lost. Before any work begins here, this needs to become a real plan file with approved scope and done criteria. Do not start coding from this document.

## Origin

During the `pipeline-hardening` plan (shipped 2026-04-11), `/plan` was invoked with a bare slug and no context. The skill drafted the plan purely from conversation state — which meant:

- The initial failure-modes list was drafted from a quick `wc -l` + one `grep` of a 1184-line file
- Recommendations ("start with the stale-state cluster") were based on 6 lines of grep output
- Mid-work, the implementation assistant had to backtrack and redo research after discovering the actual code structure didn't match the assumptions

The user flagged this mid-work and self-confirmed:

> "if /plan had done a post-approval brainstorm (per your parking-lot note), i probably would have caught the remote_finetune shape problem in that pass and proposed this sequencing from the start instead of stumbling into it mid-implementation. That's validation of the skill improvement, not just a nice thought."

## Core idea

After a plan is approved in `/plan`, and **before** any code is written, the skill should run a brief research + brainstorm pass to ground the plan in reality:

1. **Research pass** — an Explore agent (or parallel Explore agents) maps the files and surfaces that the plan will touch. Returns: file sizes, existing helpers, control flow, related tests, conventions to match. Not fixes, just a map.
2. **Brainstorm pass** — one or more brief agent runs that ask "what failure modes or constraints does the plan not yet account for, based on the research pass?" This is the pass that would have caught "`remote_finetune()` is monolithic and an attach-mode refactor is needed" *before* commit 1 started, not mid-commit-2.
3. **Plan revision** — the skill presents the research findings + brainstorm additions to the user for a second, narrower approval: "here's what I learned, does this change your plan?"

The key constraint: this happens **after** initial approval, not as a precondition. The first approval is still the "is this the right problem to solve at all" gate. The research pass answers "do we understand the problem well enough to execute cleanly."

## What needs deciding before this becomes a real plan

1. **Agent cost.** Research + brainstorm probably means 2-5 agent invocations per plan. Is that worth it on every plan, or only when the assistant flags "this might be underspecified"? User preference: likely the latter (ADHD + token efficiency memory).
2. **Brainstorm framing.** A generic "what could go wrong" brainstorm produces noise. A framing like "name 3 failure modes in the plan's 'Files to touch' that the current plan doesn't address" is more targeted. Needs prompt iteration.
3. **Skip path.** Should users be able to bypass the research pass for small plans? `/plan <slug> --no-research` or similar.
4. **Interaction with `/graphify`.** Graphify already provides the "do we understand the codebase" answer for the whole-repo case. The research pass is more surgical: "do we understand the 2-3 files this plan will touch." Don't duplicate.
5. **Failure mode of the research pass itself.** What if the research agents produce conflicting or unhelpful output? Skill needs a fallback to "proceed anyway, trust the human."

## Related artifacts

- **Origin plan**: `plans/shipped/pipeline-hardening.md` (ship hash TBD)
- **Actual skill location**: `~/.claude/skills/plan/SKILL.md`
- **User memory that led to this**: `feedback_plan_discipline.md` — "Multi-file work requires a one-screen plan file in plans/ before coding starts"

## Open questions for the actual plan author

- Is this worth a code change to the skill, or just a note in the SKILL.md that "the assistant should run research before the first code file is touched" (documentation-only fix)?
- If code: where does skill logic even live? Is the skill a pure markdown prompt, or is there code that runs agents on the user's behalf?
- Rough sketch of how a second-approval interaction should look.
