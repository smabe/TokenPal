# Smoke test: /plan skill

## Goal
Validate that the newly-built `/plan` skill works end-to-end: creates a file with the right template, blocks on approval, and can be shipped to archive. This plan is a throwaway test artifact — delete after validation.

## Non-goals
- NOT writing any real code
- NOT iterating on the skill itself — if something's wrong, we fix the skill in a followup
- NOT keeping this plan file around after validation

## Files to touch
- plans/smoke-test-delete-me.md — this file, created by the skill
- plans/shipped/smoke-test-delete-me.md — destination after ship test (optional)

## Failure modes to anticipate
- Skill fails to load (frontmatter typo) → would have errored before we got here
- Skill doesn't find plans/ directory → works because we're in the windoze repo
- Template sections drift from memory → already fixed by pointing memory at skill
- `plans/shipped/` doesn't exist → skill says it will auto-create on first ship
- Skill writes file but doesn't block on approval → biggest thing we're testing right now
- `/plan ship <slug>` fails to move the file → tested in next step

## Done criteria
- Plan file created at `plans/smoke-test-delete-me.md` ✓ (you're reading it)
- Template matches the skill's authoritative version (all 6 sections present in order)
- Skill asked for user approval instead of charging ahead
- User can run `/plan ship smoke-test-delete-me` and the file moves to `plans/shipped/`
- After ship, the skill reports success and the file is in the archive

## Parking lot
(empty)
