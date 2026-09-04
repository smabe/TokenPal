# desktop-content-contract — consent, permission preflight, and privacy contract for desktop content tools

Tracks GitHub issue #51, first sub-issue of epic #59.

## Phase map
**Phase p1 — value type, consent category, sensitive-app refusal** — SHIPPED `660e8c3`
- Enters when: start here
- Done signal: `tests/test_desktop/test_content.py` proves the value type cannot leak through `repr`/`str`/`%s` and the consent + refusal helpers return the documented errors (see shard)
- If it fails: no gate — fix-forward
- Shard: `plans/desktop-content-contract-p1.md`

**Phase p2 — unpersisted chat channel and agent/conversation gating** — SHIPPED `3a6f1e4` + `711845e`
- Enters when: p1 committed (done, `660e8c3`)
- Done signal: a marked test action run through `AgentRunner` leaves no fixture text in the trace, and `log_buddy_message(..., persist=False)` reaches the pane but not the persist callback (see shard)
- If it fails: no gate — fix-forward
- Shard: `plans/desktop-content-contract-p2.md`

**Phase p3 — `--validate` permission rows, registry invariants test, docs** — SHIPPED `7a21a7a`
- Enters when: p2 committed (done, `711845e`)
- Done signal: `tokenpal --validate` on the Mac prints Accessibility and Screen Recording rows without triggering a system prompt, and the registry-invariants test passes with zero marked tools (see shard)
- If it fails: no gate — fix-forward
- Shard: `plans/desktop-content-contract-p3.md`

## Status & cold-start
**Approval: APPROVED 2026-09-02**
**Authored at: 1de1d43**
Operator answers at approval: config keys deferred to their reading issues; completion bubble written by the persona from a content-free prompt with fixed fallback; contract-test narrowing accepted. Work starts in a later session: run `/plan desktop-content-contract`.
Verification pass 2026-09-02 — three auditors. Grounding: 99/99 claims checked, 79 ok, 20 wrong, 0 unchecked; all 20 fixed: `orchestrator.py:2554` → `2563-2564`; `agent.py` ranges for `_execute_one`/`run`/`_step`/preview/reasoning/batch loop/caps refreshed to `233-280`/`123-208`/`210-231`/`266`/`230`/`177-200`/`33-39`; `_SENSITIVE_PLACEHOLDER` at `_http.py:25`; `pop()` at `:2580`; `_helpers.py:43`; `base.py:68-70`; idle rules container named as `M1_RULES` incl. `extra_tool_names`; `CloudBackend` also built by `train_voice.py:470`; the Apple and Microsoft URL attributions reduced to what the pages say (probe carries the non-prompting claim; Windows no-grant is an inference verified on the box before ship). Executability: 12 findings, 1 blocking; blocking (bubble copy unapproved) promoted to approval with the literal string drafted in; fixed: `_make_brain` gains `agent_bridge` and the flagged-delivery branch gets a Brain-level test; same-batch network skip moved from Failure modes into Work/Done with test (5); conversation executor now refuses marked tools by name; `LogFn` Protocol carries `markup`, lambda-fallback risk named with `_noop_log` fix; Textual test specified with `run_test`; `_base.consent_error` justification corrected; `has_consent` imported at module level in `content.py`; `cli.py` imports `permissions` inside the function; `sys.executable` moved to the header line; Windows timing unified (master Done before ship, p3 may commit on its test). Coherence: 9 contradictions, all fixed: bubble "instead of" vs "plus" (Non-goals reworded); tool-drop subject (marked vs consent-gated) corrected in master; same-batch "still execute" paragraph replaced; `markup` in Protocol; Windows timing; "n/a" wording replaced with the real row text; `has_consent` binding stated in p1; contract-test narrowing added to master Locked decisions; LAN-transport claim moved from refusal text to the docs section. 0 refuted, 0 uncheckable.
Re-audit of the persona-bubble delta 2026-09-02 — 8/8 claims ok; fixed: Non-goals still said "fixed sentence" (reworded); `_desktop_done_line` budget attribute pinned to `freeform`; `filter_response` 15-char minimum noted; test (c) made writable by recording `prompt` in `_MockLLM.generate`.

p1 shipped 2026-09-02 as `660e8c3`, preceded by `b3c383d` which cleared the ruff/mypy baseline to zero per the operator's decision. **Spec check at p1** — 10/10 Work items evidenced · 2 unclaimed hunks (`test_http.py`, `test_tools.py`), both required by the described work and added to Work with the planning miss recorded. Review: external peer unavailable (Codex usage limit until 2026-09-06), so the host-native fan-out ran instead — five angles plus two verifiers, then a separate single review for the baseline commit. No receipt-backed stamp exists for either commit; the fan-out is the record. Findings applied: two prompt-injection holes in `to_prompt_block`, a `load_consent` crash path, a dropped docstring rationale, a stale `_http` docstring, a relocated regression pin, and a de-hardcoded assertion. Operator decisions at review: clean the lint baseline rather than relax the criterion; scrub desktop bodies with the content-term list; make the sensitive-app refusal generic.
**Sweep after p1** — opened `p2` and `p3`. Neither references `scrub_body`, `consent_error`, or the refusal copy, so nothing was falsified by the rename or the copy change; `p3`'s docs bullet gained the three new contract facts (envelope neutralization, content-term scrub, generic refusal). The `ruff`/`mypy` clean criteria in both shards are now literally achievable and were left as written.

p2 shipped 2026-09-02 as `3a6f1e4` (UI persist channel) + `711845e` (enforcement), split per the shard's over-cap rule. **Spec check at p2** — 13/13 Work items evidenced · no unclaimed hunks. Review: peer still unavailable (Codex limit until 2026-09-06), so host-native again — five angles, then two verification rounds over the repaired diff, 26 mutations tested. Findings applied: two real leaks into `chat_log` + logs, four broken invariants, three vacuous tests. Root cause of both leaks was one design choice the shard specified: keying redaction on the action rather than the session. Files added to Work during the phase: `tokenpal/brain/research.py` and `tokenpal/actions/research/research_action.py` (deduplicating `LogFn` surfaced three call sites that never satisfied it) — planning miss, recorded.
**Sweep after p2** — opened `p3`; it gained four carried-in items (registry/catalog parity, the second spec builder in `idle_tools_m3`, `make_agent_log` coverage now closed, console/tkinter having no chat log). `p1` is shipped and was not reopened. Nothing p2 renamed appears in `p3`'s Work.

p3 shipped 2026-09-03 as `7a21a7a`. **Spec check at p3** — 6/6 Work items evidenced · 2 unclaimed files (`tokenpal/desktop/content.py`, `tokenpal/brain/personality.py`), both docstring corrections the new docs page depends on; added to Work with the planning miss recorded. Review: peer still unavailable (Codex limit to 2026-09-06), host-native again — one correctness/docs-truth angle plus one adversarial angle that constructed violating tools, then one verification round over the repairs.

**Observable met (macOS).** `tokenpal --validate` on this Mac, no system dialog appeared:
```
  desktop tools (permissions granted to Orca)
  ✓ Accessibility: granted
  ✓ Screen Recording: granted
```
**Observable WAIVED (Windows).** Operator sign-off 2026-09-03: shipped without it. `tests/test_desktop/test_validate.py` covers the branch and pins that the probes are never called off Darwin; the real row is carried into #52 (comment on that issue) for whenever the tool gets a Windows path. Original criterion follows.

**~~Observable PENDING (Windows)~~.** `.\run.ps1 --validate` on the AMD desktop must print `no OS permission grants needed`. p3 committed on the strength of `tests/test_desktop/test_validate.py`, which covers the branch; the row is verified on the box before `/plan ship`, per the author-on-target-host rule. **This is the only outstanding Done criterion in the plan.**

All three phases shipped: p1 `660e8c3`, p2 `3a6f1e4` + `711845e`, p3 `7a21a7a`; plus `b3c383d` clearing the ruff/mypy baseline to zero at the operator's direction.

## Goal
Before any tool reads text from another desktop app (selection, document, OCR), land the consent category, a read-only permission preflight in `tokenpal --validate`, and a code-level privacy contract with tests, so every later content tool in epic #59 inherits "prompt-only, never persisted, never logged, refused in sensitive apps" instead of re-deriving it.

## Scope contract
- **Requested outcome:** a `desktop_content` consent category listed and revocable via `/consent`; `--validate` rows reporting macOS Accessibility and Screen Recording status without prompting, and a single no-grants-needed row elsewhere; a value type that carries desktop-sourced text into a prompt and cannot leak via `repr`/`str`/logging; the chat-log persister and conversation summarizer never receive desktop content or replies derived from it; a central sensitive-app refusal; a contract test every later content tool must pass; a docs section describing the contract.
- **Named semantic boundary:** the path desktop-sourced text takes from an OS read into an LLM prompt, and the persistence and log sinks it must never reach (`chat_log`, `conversation_summaries`, INFO/DEBUG log lines, the cloud research backend).
- **Explicit inclusions:** consent category + `/consent` listing; `--validate` preflight; `tokenpal/desktop/content.py` value type with `to_prompt_block()`; chat-log and summarizer exclusion; `refuse_if_sensitive`; the marker `ClassVar` on actions and the test that enumerates it; `docs/claude/actions.md` section. Issue #51 also lists a `[desktop]` config section and `[paths] allowed_dirs`; see Locked decisions for their disposition.
- **Explicit exclusions:** the content tools themselves (#52 selected text, #53 find/open, #54 documents, #55 OCR); any change to existing senses; any new UI dialog.
- **Intent class:** bounded outcome

## Non-goals
- No `[desktop]` config keys (`selection_enabled`, `ocr_fallback`) and no `[paths] allowed_dirs`. Nothing in this plan reads them; `ocr_fallback` lands with #55, `allowed_dirs` with #53/#54, and `selection_enabled` has no reader in the epic and is dropped. CLAUDE.md "Don't add state nothing reads" (CLAUDE.md, Discipline section).
- No first-use consent dialog. `/ask` has none today: it gates on `has_consent(Category.WEB_FETCHES)` and returns a one-line refusal (`tokenpal/app.py:956-959`); the CLAUDE.md claim that `/ask` shows one is stale (grep for `Continue?` in `tokenpal/` finds only the `/train` prompt at `tokenpal/app.py:480`). Desktop content follows the same shape: refusal text names what would be read and points at `/consent`.
- No change to the speech-bubble persist path (`tokenpal/ui/qt/overlay.py:1184`, `_do_log(..., persist=True)` after a bubble shows). Bubbles keep persisting; desktop-derived reply text goes through the chat-log channel with `persist=False`, and the only bubble a flagged agent session shows is a persona line generated from a content-free prompt (fixed fallback sentence).
- No in-flight LLM request cancellation when a sensitive app appears mid-reply. `tokenpal/llm/http_backend.py` has timeouts only (search `cancel` in that file: no hits); the existing per-step check in `AgentRunner.run` (`tokenpal/brain/agent.py:133-136`) is the guard this plan relies on.
- No consent category for the LAN client/server transport (`[llm] api_url`, `tokenpal/config/schema.py:116`). The p3 docs section and the CLAUDE.md bullet state that desktop content is sent to the configured inference server, which may be a remote `api_url`; no code path changes.
- No change to the three untrusted-text wrappers beyond moving `scrub_body`: `/ask` builds its own `<search_result>` envelope (`tokenpal/app.py:993-1020`) and `fetch_url` drops text on `contains_sensitive_content_term` (`tokenpal/actions/research/fetch_url.py:75`). Parked below.
- No fix to the stale CLAUDE.md `/ask` privacy claims. Parked below with evidence.

## Files touched
- `tokenpal/config/consent.py` — p1 — add `Category.DESKTOP_CONTENT`, extend `ALL_CATEGORIES`, docstring line
- `tokenpal/actions/base.py` — p1, p2 — p1: `consent_error(category_label)`; p2: `reads_desktop_content: ClassVar[bool]`
- `tokenpal/actions/network/_base.py` — p1 — `consent_error()` delegates to base
- `tokenpal/actions/network/_http.py` — p1 — `scrub_body` imported from util instead of defined here
- `tokenpal/util/untrusted_text.py` — p1 — new: `scrub_body`, `scrub_content_body`, shared `_scrub`, placeholder
- `tokenpal/util/text_guards.py` — p1 — `neutralize_envelope_tags` gains a `tag` parameter and tolerates obfuscated/attributed tags
- `tokenpal/desktop/__init__.py` — p1 — new package
- `tokenpal/desktop/content.py` — p1 — new: `DesktopContent`, `refuse_if_sensitive`, `require_consent`
- `tokenpal/desktop/permissions.py` — p3 — new: `accessibility_granted`, `screen_recording_granted`
- `tokenpal/brain/agent.py` — p2 — trace redaction, session flag, network-tool drop, reasoning suppression, `LogFn` widened
- `tokenpal/brain/orchestrator.py` — p2 — conversation tool specs exclude marked actions; agent gets all; flagged-session final delivery via unpersisted channel plus persona bubble
- `tokenpal/brain/personality.py` — p2 — `build_desktop_done_prompt()` template pair (plain + finetuned)
- `tokenpal/ui/base.py` — p2 — `log_buddy_message(..., persist=True)`
- `tokenpal/ui/qt/overlay.py` — p2 — pass `persist` through to `_do_log`
- `tokenpal/ui/textual_overlay.py` — p2 — `LogBuddyMessage.persist`, `_log_buddy`/`_append_log` gate the persist callback
- `tokenpal/app.py` — p2 — `_agent_log(..., persist=True)`; unpersisted lines logged as a length only
- `tokenpal/cli.py` — p3 — replace the Accessibility reminder block with `_check_desktop_permissions`
- `tests/_helpers.py` — p2 — `capture_logs` callback accepts `persist`; `assert_no_leak` helper
- `tests/test_actions/test_network/test_http.py` — p1 — monkeypatch target follows `scrub_body` to `tokenpal/util/untrusted_text.py`
- `tests/test_actions/test_network/test_tools.py` — p1 — same
- `tests/test_consent.py` — p1 — category round-trip
- `tests/test_desktop/__init__.py` — p1 — new
- `tests/test_desktop/test_content.py` — p1 — new
- `tests/test_agent.py` — p2 — marked-action trace, flag, reasoning, tool-drop tests
- `tests/test_qt_overlay.py` — p2 — `persist=False` reaches pane, not callback
- `tests/test_ui/test_textual_persist.py` — p2 — new: Textual `persist=False` reaches pane, not callback
- `tests/test_brain/test_tool_loop.py` — p2 — `_make_brain` gains `agent_bridge`; conversation excludes and refuses marked action; flagged agent run delivers unpersisted
- `tests/test_desktop/test_validate.py` — p3 — new
- `tests/test_desktop/test_privacy_contract.py` — p3 — new: registry invariants
- `tokenpal/desktop/content.py` — p3 — two docstrings corrected (module + `refuse_if_sensitive`)
- `tokenpal/brain/personality.py` — p3 — `contains_sensitive_content_term` docstring direction corrected
- `docs/claude/actions.md` — p3 — "Desktop content tools" section
- `CLAUDE.md` — p3 — Privacy bullet for the desktop-content tier

## Background findings
Research 2026-09-02 at 1de1d43. Sinks a user turn or tool result reaches today, which the contract must keep desktop content out of:

- **chat_log**: persistence is decided on the UI side. Every `log_buddy_message`/`log_user_message` and every shown bubble calls `_do_log(..., persist=True)` (`tokenpal/ui/qt/overlay.py:660-669,1184,1274`); `_do_log` forwards to `_chat_persist_callback` when `persist` is true (`overlay.py:1191-1202`); app wires that to `memory.record_chat_entry` (`tokenpal/app.py:1755-1763`), which no-ops when `[chat_log] persist` is off via `set_chat_log_max_persisted(0)` (`tokenpal/brain/memory.py:327-345`). Textual calls the callback unconditionally in `_append_log` (`tokenpal/ui/textual_overlay.py:1383-1388`). Agent trace lines reach the same path through `_agent_log` (`app.py:226-236`), which also logs every UI line at INFO with no truncation (`app.py:230-232`).
- **conversation_summaries**: on expiry the whole `ConversationSession.history` goes to `summarize_conversation` (`tokenpal/brain/orchestrator.py:947-971`, `tokenpal/brain/session_summarizer.py:152-186`), gated only by `contains_sensitive_term` on the output. History turns are plain `{"role","content"}` dicts with no flag (`orchestrator.py:110-151`). Tool results never enter history; only the assistant reply does (`orchestrator.py:1692-1697`, `2563`).
- **Logs**: full reply at INFO (`orchestrator.py:2564`, right after `add_assistant_turn` at `:2563`), tool result at DEBUG truncated to 200 chars (`orchestrator.py:1760-1763`), agent trace shows reasoning verbatim (`tokenpal/brain/agent.py:230`) and 240 chars of each tool result (`agent.py:266`).
- **Cloud**: conversation and agent turns never reach `CloudBackend`; it is constructed only by research (`tokenpal/actions/research/research_action.py:307,399`) and the voice trainer (`tokenpal/tools/train_voice.py:470`). The leak vector is the agent calling `research`/`research_followup` with content in the argument when `[cloud_llm] enabled`.
- **Idle/ambient tool paths**: `M3_CATALOG` is a hardcoded tuple (`tokenpal/brain/idle_tools_m3.py:34-44`) and idle rules name tools explicitly in `M1_RULES` (`tokenpal/brain/idle_rules.py:286-644`, via `tool_name=` and also `extra_tool_names=`, e.g. `:643`), so a new action is unreachable ambiently unless someone lists it. p3's invariants test pins that.
- **Sensitive kill switch**: checked at turn start (`orchestrator.py:2466-2476`) and per agent step (`agent.py:133-136`); `_clear_conversation` runs synchronously on the loop thread (`orchestrator.py:1019-1028`).
- **Already safe** (no turn text): `tool_calls`, `observations`, `session_summaries`, `daily_summaries`, `app_enrichment`, `active_intent`; TTS streams PCM to the device and writes nothing (`tokenpal/audio/tts.py:76-140`).
- **Permissions**: `HIServices.AXIsProcessTrusted()` is already called at `tokenpal/senses/_keyboard_bus.py:33-37` with an ImportError fallthrough. `Quartz.CGPreflightScreenCaptureAccess()` imports from the repo venv and returned without a dialog (probe 2026-09-02, this Mac, both `True`). Apple's reference page https://developer.apple.com/documentation/coregraphics/cgpreflightscreencaptureaccess() carries only the declaration; the non-prompting behavior rests on that probe and on Apple shipping the separate `CGRequestScreenCaptureAccess` for prompting. Windows: the UI Automation security page (https://learn.microsoft.com/dotnet/framework/ui-automation/ui-automation-security-overview) discusses only the `uiAccess` manifest for reaching higher-privilege UI and says nothing about `PrintWindow`; that Windows has no TCC-style grant for these reads is an inference, and the n/a row is verified on the Windows box before ship.
- **Consent plumbing**: categories are constants plus `ALL_CATEGORIES` (`tokenpal/config/consent.py:26-42`); `load_consent`/`save_consent` filter on that tuple; `/consent` builds its picker from `ALL_CATEGORIES` with no per-category UI wiring (`tokenpal/app.py:1341-1384`). Action-side refusal is `consent_error()` in `tokenpal/actions/network/_base.py` (`:10-12` after p1).

## Locked decisions
- **Config keys deferred.** `[desktop] selection_enabled`, `[desktop] ocr_fallback`, `[paths] allowed_dirs` are not added here. Evidence: no reader exists in this plan; CLAUDE.md Discipline "Don't add state nothing reads". Two have readers in the epic and land with them: #55 reads `ocr_fallback`, #53/#54 read `allowed_dirs`. `selection_enabled` has no reader in any epic issue (revoking `desktop_content` consent already disables the read), so it is dropped unless #52 finds a need for a second toggle. Operator confirmed at approval 2026-09-02.
- **Desktop content never enters `ConversationSession.history`.** Marked actions are excluded from the conversation path's tool specs (`orchestrator.py:352`) and are reachable only from `/agent` and from slash commands that call the LLM directly (#52's `/proofread`). Rationale: history feeds the summarizer, the bubble, and the INFO reply log, three sinks with no per-turn flag; excluding the path is smaller and provable, versus adding a flag to message dicts that are sent verbatim to the LLM server.
- **Unpersisted channel is `log_buddy_message(text, persist=False)`.** The `persist` parameter already exists on `_do_log` (`overlay.py:1191-1198`); this plan exposes it one level up. Textual gains the same gate.
- **Agent sessions that read desktop content deliver the final answer through the unpersisted channel plus a persisted bubble the persona writes from a content-free prompt.** Operator direction 2026-09-02: "Let the llm dictate the finish state using its persona." The bubble comes from one extra `generate` call on a new `build_desktop_done_prompt()` template that carries identity, mood, and the fact that the answer is in the chat log, and nothing else, so it cannot leak by construction; when the reply is filtered out or the call fails, the fixed fallback "Done. The answer is in the chat log and was not saved." is shown.
- **Sensitive-app filter for source apps is `contains_sensitive_term`** (full `SENSITIVE_APPS`, `tokenpal/brain/personality.py:285-290`), matching the kill switch (`check_sensitive_app`, `personality.py:769-771`).
- **Desktop bodies are scrubbed with `contains_sensitive_content_term`, not the full app list.** Revised at p1 review, operator decision 2026-09-02. `scrub_body` keeps the app-name list for the seven network tools and `display_text`; `scrub_content_body` (same module) uses the narrower identity-critical list for long-form prose. Evidence: the comment above `SENSITIVE_CONTENT_TERMS` (`personality.py:264-277`) names "signal", "health", "chase", "fidelity", "keeper" as false positives on ordinary prose, and every other untrusted-content path already uses the narrow list (`app.py:991`, `fetch_url.py:75`, `brain/research.py:634`, the three network senses).
- **The sensitive-app refusal does not name the app.** Revised at p1 review, operator decision 2026-09-02. `refuse_if_sensitive` returns "Won't read from that app: it's on the sensitive-app list." The result reaches the model and is DEBUG-logged twice (`orchestrator.py:1694`, `:1759`), so naming the app would leak what the refusal protects. Matches `list_processes.py:53` and `senses/process_heat/sense.py:82`, which both substitute a generic label.
- **The `<desktop_content>` envelope is neutralized against forged tags.** `to_prompt_block` runs `neutralize_envelope_tags(..., "desktop_content")` over the scrubbed body and strips `"`/`<`/`>` plus line separators from both interpolated attributes. `tokenpal/util/text_guards.py` gained a `tag` parameter for this; `<transcript>` behavior for its two existing callers is unchanged.
- **`--validate` reports a missing Screen Recording grant as a warning, not a problem.** No shipped tool needs it until #55.
- **After a marked action runs, tools whose catalog entry names a `consent_category` are dropped from the agent's tool list for the rest of that run** (`tokenpal/actions/catalog.py:26-29`, `find_entry` at `catalog.py:276-282`). Actions without a catalog entry stay available. A network tool requested in the same LLM response as the content read is not invoked; it returns a fixed "skipped" result.
- **The conversation path both hides and refuses marked actions.** They are absent from its tool specs and `_execute_tool_call` (`orchestrator.py:1751-1756`) returns an error string if the model names one anyway.
- **The shared contract test pins static invariants plus consent-first ordering; refusal and no-leak over a real OS read are each tool's own tests.** Issue #51 asked for all four checks in the parametrized test; a generic test cannot supply valid arguments or a fake OS read for an unknown tool. Operator accepted the narrowing at approval 2026-09-02 ("Narrow is ok").

## Done criteria
- All three shards' Done criteria met and committed, one commit per phase.
- `tokenpal --validate` on the Mac shows both permission rows as granted/missing with no system dialog (p3 Done). On the Windows box it prints the no-grants-needed row, verified there before `/plan ship` per the author-on-target-host rule; p3 may commit on the strength of its test with that row marked pending in Status.
- `/consent` lists `desktop_content`; revoking it makes `require_consent()` return the consent error (p1 test plus a manual `/consent` check in the running buddy).
- `pytest`, `ruff check tokenpal/`, `mypy tokenpal/ --ignore-missing-imports` green at the end of each phase. Operator decision 2026-09-02: the pre-existing baseline (10 ruff `N802`, 38 mypy) is cleaned in its own commit ahead of p1 rather than the criterion being relaxed, so "green" means zero from p1 onward.

## Parking lot

**Dispositioned at ship 2026-09-03.** Filed as issues:
- **#60** — `register_action` silently overwrites on duplicate `action_name`.
- **#61** — CLAUDE.md's `/ask` privacy claims are false (no first-use warning; queries *are* persisted when `[chat_log] persist` is on).
- **#62** — `/idle_tools roll` raises `AttributeError` on `Brain._generate_tool_riff`, left by `9cefbc3`. The repo's only remaining mypy error; operator deprioritised the fix, filed so it is not mistaken for new breakage.

Carried into **#52** as a comment (each goes live with the first marked tool): `discover_actions` swallowing `ImportError` so the contract test fails open per host; `_imported_modules` failing open the same way; `http_backend.py:448` logging raw tool-call arguments below the agent layer; `permissions.py` conflating a missing pyobjc with a raising probe; and the unverified Windows `--validate` row.

Resolved at ship: the `--validate` header naming `sys.executable` rather than the responsible parent process — fixed in `f20ab24`, now names `TERM_PROGRAM` like the microphone row.

Dropped, with reason:
- **Unify the three untrusted-text envelopes.** They now differ in tag *and* in scrub predicate (`scrub_body` vs `scrub_content_body`); a shared helper would need three shapes for no correctness gain.
- **`_keyboard_bus` reusing `desktop.permissions.accessibility_granted`.** Same call, different purpose (warmup vs status); two lines of duplication.
- **Research tools forwarding conversation text to the cloud.** Pre-existing, consented, and an explicit Non-goal of this plan.

### Original entries

- **`discover_actions` swallows `ImportError`, so the contract test fails open per host** (`tokenpal/actions/registry.py:33-34`). A marked tool whose module imports `AppKit`/`Quartz` at module scope is silently unregistered on Linux/Windows CI — `_MARKED` is empty and all parametrized cases report "skipped (empty parameter set)", visually identical to a healthy run. #52's selection reader will have exactly that import shape. Fix belongs with #52: either assert `_MARKED` is non-empty once the first tool ships, or have the registry record import failures and assert none.
- **`action_name` collisions are silent** (`tokenpal/actions/registry.py:20`, a plain dict assignment). Two modules registering the same name — a plausible `darwin_impl`/`windows_impl` split, matching the sense convention — means only the last-imported one is ever contract-tested. Cheap fix: raise on duplicate registration.
- **`--validate`'s desktop header names `sys.executable`, but macOS TCC attributes grants to the responsible parent process.** The audio block in the same file names `TERM_PROGRAM` for that reason. Both grants read `True` here for a bare `python -c`, consistent with inheritance from the terminal rather than a grant on `python3`. A user who reads "granted for .../python3" and later launches from a different terminal can get a different answer. Operator decision before #52 ships; the row was specified verbatim by the p3 shard so it was not changed unilaterally.
- **`permissions.py` reports `unknown (pyobjc unavailable)` when the bindings are present but the call raises.** Both functions collapse `ImportError` and `Exception` into `None`, and `cli.py` renders one message. A user on a macOS release where `CGPreflightScreenCaptureAccess` throws is told to install something already installed.
- **`_imported_modules` fails open** on an unimportable submodule, a module with no `__file__`, or a C extension — the module is treated as importing nothing. Bounded by the fact that everything under `tokenpal/actions/` is plain in-tree Python today.
- **Three untrusted-text envelopes.** `wrap_result` (`tokenpal/actions/network/_http.py:130-132`), `/ask`'s `<search_result>` (`tokenpal/app.py:993-1020`), `fetch_url` (`tokenpal/actions/research/fetch_url.py:75`). Useful to unify; not required because `DesktopContent.to_prompt_block` reuses `scrub_body` and needs its own tag.
- **CLAUDE.md `/ask` claims are stale.** "shows an explicit first-use consent warning" has no code behind it (`tokenpal/app.py:956-959`), and "queries never persisted to disk" is false when `[chat_log] persist` is on: `/ask` logs 500 chars of the result with `persist=True` and injects `[User ran /ask: query]` as a user turn (`tokenpal/app.py:1005-1023`), which then reaches `conversation_summaries`. Fix belongs with an `/ask` change, not here.
- **`http_backend.py:448` logs raw tool-call arguments at WARNING on JSON failure.** Once a content tool exists, an argument could carry content. Confirmed at the p2 review: this sits *below* the agent layer, so no session flag can reach it, and it bypasses the 80-char `fmt_args` cap that bounds the trace. Long unescaped strings are exactly when a local model's tool-call JSON malforms, so the trigger and the payload correlate. Pre-existing and unreachable until a marked tool ships; revisit in #52.
- **`_keyboard_bus._warmup_pynput_darwin_axtrust` could call `desktop.permissions.accessibility_granted`.** Same call, different purpose (warmup vs status); not required.
- **Research tools invoked from the conversation path** can already forward chat text to the cloud when the user has enabled cloud research. Pre-existing and consented; out of scope.
