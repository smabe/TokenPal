# /plan skill

## Goal
Build a `/plan <feature-name>` slash command that creates `plans/<slug>.md` from a template, blocks on user approval, and becomes the session's reference for scope checking.

## Non-goals
- NOT building scope-creep enforcement logic — that's behavioral, handled by the working agreement + ADHD memory, not code
- NOT integrating with other skills (tdd, simplify, commit) — they work independently
- NOT making it project-local — goes in `~/.claude/skills/` so it works across all projects

## Also in scope (post-approval)
- Archive to `plans/shipped/` when done criteria are met (auto-move, don't delete)

## Files to touch
- `~/.claude/skills/plan/SKILL.md` — the skill definition (new)
- `plans/plan-skill.md` — this file, for meta-demonstration

## Failure modes to anticipate
- Skill frontmatter format wrong → skill won't load → need to mirror an existing skill's format
- Template in skill drifts from the one in `feedback_plan_discipline.md` memory → single source of truth: the skill references the memory, doesn't duplicate it
- User invokes `/plan` with no arg → skill prompts for a slug instead of erroring
- Plan file already exists for the given slug → ask before overwriting
- `plans/` directory doesn't exist → skill creates it
- Slug has spaces or weird characters → normalize (lowercase, hyphens)

## Done criteria
- `~/.claude/skills/plan/SKILL.md` exists
- Invoking `/plan <name>` in any project creates `plans/<slug>.md` with the template filled in based on conversation context
- User approval is required before the plan is "live"
- Template matches `feedback_plan_discipline.md` exactly
- Manual smoke test: run `/plan test-feature` in a scratch directory, verify file is created correctly

## Parking lot
(empty)
