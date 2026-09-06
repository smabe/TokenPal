# Resume — read-document-54 (briefed 2026-09-06, after harness-tool-policy)

```
harness-tool-policy is shipped — p1 3bbc46d, p2 38d9d68, p3 e8e2692, p4 1094948,
plus 2a6ff2e. Suite 2439 -> 2536. Cleanup done: plan + 4 shards archived to
plans/shipped/harness-tool-policy*.md, memory rewritten as a shipped record,
#74 closed with evidence, follow-ups filed as #76, #77, #78.

NOTHING IS PUSHED. 16 commits sit on local main, and #66, #60 and #61 carry
"Fixes #" trailers that will not fire until they reach GitHub. Push before
anything else, or those three stay open and look unfixed.

What changed that matters for the next tool you write: AbstractAction now
declares allow_unprompted, writes_durable_sink, path_params, path_roots,
path_screen. ToolInvoker.invoke is the only dispatch point; it resolves every
declared path into a ResolvedPath (raw/resolved/root/rel) before the tool runs.
A new filesystem tool gets containment by DECLARING it — read
docs/claude/actions.md's "Filesystem path policy" section before writing one,
and tests/test_actions/test_path_policy_contract.py will fail you if you take a
path-shaped argument without declaring a policy.

Next steps available (pick one or propose your own):
- #54: read_document — vendor Hermes read_extract + [paths] allowed_dirs.
  Next in epic #59's order, and materially cheaper now: it declares
  path_params/path_roots and inherits containment instead of writing it.
- #27: tool subsetting — pick a category before exposing tools to the LLM.
  The epic says to do this before the tool count grows further; it is now 41.
- #77: test-contract hardening — test_privacy_contract.py's three fail-open
  modes, the duplicated _dummy_args, and 11 Brain.__new__ fixtures.
- #76: content-based secret detection — every screen shipped so far matches on
  NAMES, so a credential inside notes.txt is still returned.
- #53 p4: Windows Search backend for find_files. Operator parked this on
  2026-09-05 ("not doing 53 p4 any time soon") — do not start it unasked.

Recommended next pickup: #54. It is the epic's stated order, the harness work
just made it smaller, and it is the first real test of whether "a new path tool
is contained by declaring" actually holds for a tool nobody had in mind when
the policy was written. If it needs an escape hatch, that is a finding about
the harness, not about #54.

Read first if continuing from this work:
- ~/.claude/projects/-Users-smabe-projects-windoze/memory/project_harness_tool_policy.md
- docs/claude/actions.md — the "Filesystem path policy" section
- gh issue view 54
- plans/shipped/harness-tool-policy-p3.md — its Decisions & findings explain why
  the resolver returns a NamedTuple and why path_screen governs the raw name only

Open questions / blockers: none. Codex peer quota resets this evening
(2026-09-06), after which /auto-review returns to real cross-family review
instead of the same-family fallback used all through this plan.
```
