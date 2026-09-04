# Resume — selected-text-primitive / #52 (briefed 2026-09-03, after desktop-content-contract #51)

```
#51 (desktop content consent + privacy contract) shipped 2026-09-03 on main,
unpushed. Commits: 660e8c3 contract, 3a6f1e4 + 711845e enforcement, 7a21a7a
preflight/tests/docs, plus b3c383d (ruff+mypy baseline to zero) and f20ab24.
Plans archived to plans/shipped/desktop-content-contract*.md; memory rewritten;
follow-ups filed as #60, #61, #62 with five more carried onto #52 as a comment.

Recommended next pickup: #52 — selected-text primitive + /proofread and /explain
on the previously focused app. It is the first tool that actually exercises
everything #51 built, and #51's contract test will start measuring it the moment
it registers.

Read first:
- docs/claude/actions.md § "Desktop content tools" — the author checklist. The
  call order is require_consent() -> refuse_if_sensitive(app, title) as soon as the app is known -> OS read ->
  DesktopContent(...).to_prompt_block(). Set reads_desktop_content = True and
  cacheable = False; everything else (trace redaction, cache bypass, network-tool
  drop, unpersisted delivery, conversation-path refusal) is automatic.
- The "Carried in from #51" comment on issue #52 — five things that go live with
  the first marked tool. The one that will bite first: discover_actions swallows
  ImportError (tokenpal/actions/registry.py:33-34), so a tool importing
  AppKit/Quartz at module scope is silently unregistered on non-Mac CI and the
  whole contract test evaporates into a green run. Assert _MARKED is non-empty
  once the tool lands.
- tests/test_desktop/test_privacy_contract.py — what your tool will be checked
  against. It was adversarially probed (the first version passed a plausible
  unsafe OCR tool), so if you extend it, extend the probes too.

Other open work, if #52 is not the priority:
- #60 — register_action silently overwrites on duplicate action_name. Small,
  self-contained; matters more once #52 lands a platform-split tool.
- #61 — CLAUDE.md's /ask privacy claims are false (no first-use warning exists;
  queries ARE persisted when [chat_log] persist is on). Docs or code, your call.
- #62 — /idle_tools roll raises AttributeError on Brain._generate_tool_riff,
  left by 9cefbc3's extraction. Currently the repo's ONLY mypy error. Likely fix
  is brain._idle_runner._riff(snapshot, result); needs a judgment call on intent.

Open decisions: none blocking. The Windows `tokenpal --validate` row
("no OS permission grants needed") is waived, not verified — the test covers the
branch, the real row is carried into #52 for whenever it gets a Windows path.

State: main is 25 commits ahead of origin and unpushed. pytest 2171 passed,
ruff clean, mypy 1 error (that's #62).
```
