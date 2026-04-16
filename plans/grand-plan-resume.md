
  The TokenPal grand plan is shipped — all six phases landed on 2026-04-15,
  ending with commit 1eb6c52 (Phase 6 polish + bundled refactors) and a50baf1
  (docs/agents-and-tools.md + README updates). All plan files moved to
  plans/shipped/. Memory rewritten. Follow-ups filed:

  - #26: Phase 6 polish follow-ups (epic checklist) — usage-pattern riffs,
    rate-limit queue mode, cross-session agent cache, /research --fresh,
    required catalog `kind`.
  - #27: Tool subsetting — pick a category before exposing tools to the LLM
    (gemma4's 8-tool cliff will bite once users enable the full utility set).

  Recommended next pickup: #27 if the user has been enabling a lot of utility
  tools (it's the only thing that actually fails at registry size), otherwise
  cherry-pick a single item from #26 — `/research --fresh` is the smallest
  (~30 min) and the catalog `kind` required-field change is ~15 min of mechanical
  cleanup.

  Read first if continuing:
  - docs/agents-and-tools.md (canonical reference for /agent, /research, tools)
  - gh issue view 26 (checklist)
  - gh issue view 27 (subsetting design)
  - ~/.claude/projects/-Users-smabe-projects-windoze/memory/project_grand_plan.md
    (shipped record, not resume prose)

  Also outstanding (user-filed this session while dogfooding, unrelated to the
  grand plan): #18-#25 — installer VRAM detection on RTX 5090, voice training
  edge cases, server persistence, ASCII markup leak, tool discoverability UX.
  Worth a triage pass before diving into #26/#27.

  Uncommitted: plans/shipped/ has the archived plan files staged as D/??
  moves — run `git add -A plans/ && git commit -m "archive phase 6 + grand plan
  + next-batch brainstorms to plans/shipped/"` before the next feature.