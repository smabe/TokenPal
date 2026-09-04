# Resume — find-files-open-path / #53 (briefed 2026-09-04, after selected-text-primitive #52)

```
#52 (selected-text primitive, read_selection, /proofread + /explain) is
shipped — phases eb20f7b, d5172ca, 750a164; plan archived by 10c52ce
("Fixes #52", closes when main is pushed). Live checks all passed on the Mac
2026-09-04. Cleanup done: plans under plans/shipped/selected-text-primitive*,
memory rewritten as a shipped record, follow-ups filed as #63, #64, #65.
main is ~35 commits ahead of origin and unpushed. Suite 2222 passed, ruff
clean, mypy 1 error (#62).

Next steps available (pick one or propose your own):
- #53: find_files + open_path actions — next in epic #59's order; open_path is
  the first confirm-gated side effect, find_files needs [paths] allowed_dirs.
- #62: /idle_tools roll raises AttributeError on Brain._generate_tool_riff —
  the repo's only mypy error, 20-minute fix, likely
  brain._idle_runner._riff(snapshot, result).
- #60: register_action silently overwrites on duplicate action_name — small;
  matters more once #53 lands platform-split tools.
- #61: CLAUDE.md's /ask privacy claims are false — docs or code.
- #63: Windows UI Automation read — author on the AMD desktop only.

Recommended next pickup: #62 as a warm-up (clears mypy to zero), then #53.

Read first if continuing from this work:
- ~/.claude/projects/-Users-smabe-projects-windoze/memory/project_selected_text_52.md
- docs/claude/actions.md § "Desktop content tools" — the author checklist,
  now with read_selection as the reference implementation and the envelope
  cap rule; #53's open_path is NOT a content tool (no marker) but find_files
  must never return file contents.
- gh issue view 53 --comments, and epic #59 for the ordering.

Open questions or blockers: the three phase commits and the seal/ship
commits carry Co-Authored-By / Claude-Session trailers because the harness
instructed it this session; amend before pushing if you still want them off.
```
