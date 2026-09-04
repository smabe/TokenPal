# find_files + open_path (#53)

## Phase map
**Phase p1 — shared path safety + `[paths] allowed_dirs`** — SHIPPED
- Enters when: start here
- Done signal: `tests/test_util/test_paths.py` proves symlink, `..`, and case escapes are refused; `read_file` and `grep_codebase` import the shared helpers and their suites stay green
- If it fails: no gate — fix-forward
- Shard: `plans/find-files-open-path-p1.md`

**Phase p2 — `find_files` action (Spotlight on macOS, bounded walk elsewhere)** — SHIPPED
- Enters when: p1 shipped
- Done signal: the shard's temp-tree test shows protected and sensitive paths absent from results, and a live `find_files` in `/agent` on this Mac returns newest-first paths from `~/Downloads`
- If it fails: no gate — fix-forward
- Shard: `plans/find-files-open-path-p2.md`

**Phase p3 — `open_path` action + confirm gate on the conversation path** — NEXT
- Enters when: p2 shipped
- Done signal: the issue's scenario runs end to end in `/agent` on this Mac with the confirm modal showing the path; plain chat "open it" and `open_app` both prompt
- If it fails: no gate — fix-forward
- Shard: `plans/find-files-open-path-p3.md`

**Phase p4 — Windows Search index backend for `find_files` (written here, verified on the AMD desktop later)**
- Enters when: p2 shipped (independent of p3)
- Done signal: the SQL builder and its escaping are unit-tested on the Mac; the COM path is guarded so any failure drops to the p2 walk; the module header states it is unverified on Windows (the ship step files the AMD-desktop follow-up)
- If it fails: no gate — fix-forward
- Shard: `plans/find-files-open-path-p4.md`

## Status & cold-start
**Approval: APPROVED 2026-09-04**
**Authored at: f27db56**
Research 2026-09-04: one primary investigator (conversation tool path, config, registry/catalog, test conventions), one security-boundary specialist (containment probes, `open` handler probes, Spotlight injection probe, persistence trace), one Windows Search specialist (Microsoft Learn citations). Live `mdfind` probes run on this Mac by the planning session.
Verification pass 2026-09-04 — grounding 60/73 resolved, 11 drifted, 2 unchecked; executability: p1 2 gaps, p2 2, p3 4, p4 0; coherence 10. All fixed in one pass (named below) except the PATHEXT default and the crawl-scope behavior, which stay tagged [unverified] for the AMD-desktop follow-up. Fixed: containment no longer relies on `os.path.normcase` (identity on POSIX; probe showed an upper-cased root failing `is_relative_to`) → strict on POSIX, `normcase` on Windows; `resolve_inside` returns `(resolved, root)`; p2/p3 patch `tokenpal.util.paths.git_root` and `find_files` imports `load_config` at module top; p3 sets `requires_confirm = False` on the existing tool-loop stubs; p3 bundle test uses an allowlisted suffix; p3 adds hidden/Library/env/credentials refusal cases; p4 drops character stripping in favour of escaping and maps `image`→`picture`; p4 guard is any `Exception`; master Done 3 replaces `signal-export.txt` (benign under the narrow list) with `1password-export.csv`; `~/.ssh` and `~/Library/Mail` DO return Spotlight rows (exclusion by prefix is load-bearing); injection probe restated as predicate truncation; grep/line-hint drifts corrected.
Decided at approval 2026-09-04 (operator): `open_path` uses a **denylist** — no scripts, executables, installers, or launchers — because opening a file in a text editor or browser is useful and an allowlist would keep blocking new document types. Exec-bit and app-bundle checks stay. Modal title "Confirm tool call" approved.
Handoff 2026-09-04: approved plan committed as 5ee540a; no source changed since Authored at (`git log f27db56.. -- tokenpal/ tests/ config.default.toml docs/ CLAUDE.md` is empty), so every anchor in the shards is current. Phase workers run on `model="opus"` (operator instruction 2026-09-03). The Codex review peer is out of quota until the evening of 2026-09-06; `/auto-review` falls back to the host-native fan-out until then.
**Spec check at p1** — 7/7 Work items evidenced · none unclaimed.
p1 shipped 2026-09-04. Simplify: 4 applied, 6 rejected with evidence, 4 parked. Review: Codex peer out of quota until 2026-09-06, so `/auto-review` ran its host-native fan-out (line-by-line, removed-behavior, cross-file tracer, CLAUDE.md conventions) — no external-peer receipt, no commit-gate stamp. Six defects confirmed by probe and fixed in p1's own code; see the p1 shard's `## Decisions & findings` for all of them. The severe one: `allowed_dirs` written as a bare TOML string iterated character by character and admitted `/` as a root.

Operator decisions at the p1 review gate (2026-09-04): **(1)** widen `path_is_sensitive` to key material + password managers, leaving `signal`/`health` allowed; **(2)** do NOT fix `read_file`'s relative-path symlink escape here — filed as **#66**; **(3)** change `resolve_inside` to the 3-tuple `(resolved, root, rel)` — p2/p3 shard text swept to match.

**Spec check at p2** — 6/6 Work items evidenced · none unclaimed.
p2 shipped 2026-09-04. Simplify: 4 angles, ~10 applied. Review: Codex peer still out of quota (re-probed; resets 2026-09-06 22:26), so `/auto-review` ran its host-native fan-out — **no external-peer receipt, no commit-gate stamp**. Seven confirmed defects fixed, including two that made the tool behave differently per platform: the UTI kind tables disagreed with `_KIND_EXTS` (macOS answered `kind="document"` with `.py`/`.json`), and "newest first" ranked an arbitrary slice on any query broader than the candidate cap. Full list in the p2 shard.

Operator decisions at the p2 review gate (2026-09-04): **(1)** the macOS Spotlight predicate's content clause makes `find_files` a content oracle — **accepted as-is**, recorded in `CLAUDE.md`'s Privacy section; **(2)** an explicitly empty `[paths] allowed_dirs` now disables the file tools (changes p1's `allowed_roots`); **(3)** the subprocess-with-timeout duplication was **extracted across all five call sites** into `tokenpal/util/proc.py::run_capture`, not filed as a follow-up.

Carried into p3: `open_path` gets the same widened root set, so the empty-list opt-out and the always-appended git root both apply to what the confirm modal will offer. `resolve_inside` returns `(resolved, root, rel)` — take `rel` from the match. Gate `kind`/type checks on the RESOLVED path, never the candidate: a `report.pdf` symlink to a `.md` is the case that bit p2.

NEXT: p3 — read `plans/find-files-open-path-p3.md` FIRST.

## Goal
Two opt-in tools so "find that PDF from this week and open it" works in `/agent` and in plain chat: `find_files` returns paths under user-allowlisted roots, newest first, never contents; `open_path` opens a document with the OS default app after a confirm modal, and refuses anything that would run a program. Plain chat gains the confirm gate `/agent` already has, which also stops `open_app` launching unprompted from chat.

## Scope contract
- **Requested outcome:** `find_files` and `open_path` registered, catalogued, opt-in via `[tools] enabled_tools`, working from `/agent` and from chat with the confirm modal in both; `[paths] allowed_dirs` in config; Spotlight on macOS, a bounded directory walk elsewhere, a Windows Search index path written now and verified on the AMD desktop later.
- **Named semantic boundary:** the LLM tool registry and catalog; the conversation tool executor (`Brain._execute_tool_call`) which today has no confirm gate; the path-safety helpers private to `read_file`/`grep_codebase`; the `[paths]` config section.
- **Explicit inclusions:** `tokenpal/util/paths.py`; `PathsConfig.allowed_dirs`; `tokenpal/actions/find_files.py`; `tokenpal/actions/open_path.py`; `tokenpal/util/windows_search.py`; catalog entries; the confirm gate wired to the existing `AgentBridge.confirm_callback`; a neutral confirm-modal title; docs.
- **Explicit exclusions:** content search inside files beyond what the index gives for free; delete/move/rename; widening `open_app`'s allowlist; changing `filesystem_pulse` to emit paths; `read_document` (#54); fixing #60 duplicate registration; a per-action hook to pre-resolve the confirm prompt (operator declined: the modal shows the raw argument); Linux AT-SPI or `fd` integration.
- **Intent class:** bounded outcome.

## Non-goals
- No change to `read_file`'s filter policy: it keeps the broad `contains_sensitive_term` on `path_arg` (`tokenpal/actions/read_file.py:75`) and its git-tracked-only scope. Only its two helpers move.
- No new UI: nothing in the UI or first-run wizard reads `[paths]` (`grep -rn "config.paths\|\.paths\." tokenpal/ui/ tokenpal/first_run.py` → no hits at f27db56; `first_run.py` takes `data_dir` as a plain parameter).
- No fix for #60: each new tool is one class branching on `current_platform()` inside `execute`, the `open_app.py:77-94` shape, so no second class ever registers the same name.
- No `action_configs` wiring in `app.py:228-231` (the call passes none today; `memory_query`'s `data_dir` is likewise never injected). Tools read `load_config().paths.allowed_dirs` at execute time, the `tokenpal/actions/utilities/sunrise_sunset.py:17-23` precedent.
- Windows verification is not a Done criterion of this plan. p4 ships guarded and unverified; the ship step files the follow-up issue.

## Files touched
- `tokenpal/util/paths.py` — p1 (new) — reject regex, git root, `allowed_roots`, `resolve_inside`, `is_hidden_or_protected`, `path_is_sensitive`
- `tokenpal/actions/read_file.py` — p1 — import `REJECT_PATH`/`git_root` from util; delete the private copies
- `tokenpal/actions/grep_codebase.py` — p1 — import `git_root` from util; delete the private copy
- `tokenpal/config/schema.py` — p1 — `PathsConfig.allowed_dirs`
- `config.default.toml` — p1 — `[paths] allowed_dirs` with a comment
- `tests/test_util/__init__.py` — p1 (new) — package marker for the new test directory
- `tests/test_util/test_paths.py` — p1 (new) — containment and filter tests
- `tokenpal/actions/find_files.py` — p2 (new), p4 (windows branch calls `windows_search`)
- `tokenpal/actions/grep_codebase.py` — p1 (import `git_root`), p2 (fix the duplicated per-file-cap flag and the `>=` off-by-one; operator-expanded 2026-09-04)
- `tokenpal/actions/catalog.py` — p2, p3 — two `LOCAL_SECTION` entries
- `tests/test_actions/test_catalog.py` — p2, p3 — pinned `LOCAL_SECTION` name set
- `tests/test_actions/test_find_files.py` — p2 (new), p4 (fallback-on-com_error test)
- `tokenpal/actions/open_path.py` — p3 (new)
- `tokenpal/brain/orchestrator.py` — p3 — confirm gate + lock in `_execute_tool_call`
- `tokenpal/app.py` — p3 — neutral confirm-modal title in `_agent_confirm`
- `tests/test_actions/test_open_path.py` — p3 (new)
- `tests/test_brain/test_tool_loop.py` — p3 — confirm-gate tests on the conversation path
- `docs/claude/actions.md` — p3 — the two tools, the chat confirm gate, the persistence note
- `CLAUDE.md` — p3 — one Privacy line on `[paths] allowed_dirs` and path persistence
- `tokenpal/util/proc.py` — p2 (new) — `run_capture`, the shared subprocess-with-timeout runner (operator-expanded 2026-09-04; also migrated `git_log`, `git_nudge`, `git_sense`)
- `tokenpal/brain/git_nudge.py` — p2 — `_git`/`_git_exit_code` migrated onto `run_capture`
- `tokenpal/senses/git/git_sense.py` — p2 — `_git`/`_is_dirty` migrated onto `run_capture`
- `tokenpal/actions/git_log.py` — p2 — `_run_git` migrated onto `run_capture` (also fixes a kill-without-reap zombie leak)
- `tests/test_util/test_proc.py` — p2 (new) — streams, non-zero exit, missing binary, timeout kills and reaps
- `CLAUDE.md` — p2 — Privacy line recording the accepted content-oracle exception (p3 adds its own)
- `tokenpal/util/windows_search.py` — p4 (new)
- `tests/test_util/test_windows_search.py` — p4 (new)

## Background findings
- **Conversation tool calls run in parallel** via `asyncio.gather` (`tokenpal/brain/orchestrator.py:1682-1684`); the agent path runs them sequentially precisely so confirm prompts do not stack (`tokenpal/brain/agent.py:222-224`). Qt creates a fresh `ConfirmDialog` per call (`tokenpal/ui/qt/overlay.py:783-789`), so two gated calls in one round would stack modals. p3 serializes the confirm await with a lock and leaves the gather alone.
- **The confirm callback already reaches Brain**: `self._agent.confirm_callback` (`orchestrator.py:410`, `AgentBridge` at `:225-230`), wired from `app.py:342` to `_agent_confirm` (`app.py:270-296`), which creates a future on the running loop and returns `False` when the overlay has no modal (`:291-294`). Both `/agent` and chat run on the brain thread's loop (`app.py:1813-1817`, `orchestrator.py:510`). Denied text to mirror: `f"User denied {tc.name}."` (`agent.py:326-330`).
- **Actions get `{}` as config**: `app.py:228-231` calls `resolve_actions` without `action_configs`. Precedent for reading config in a tool: `_load_default_latlon` calls `load_config()` at execute time (`tokenpal/actions/utilities/sunrise_sunset.py:17-23`).
- **`_git_root` is duplicated verbatim** in `read_file.py:33-47` and `grep_codebase.py:18-32`; `_REJECT_PATH` (`read_file.py:15`) has no other importer (`grep -rn "_REJECT_PATH\|_git_root" tokenpal/ tests/`).
- **Containment probe (this Mac, temp tree):** a symlink inside a root pointing at `~/.ssh/id_rsa`, and `root/../secret`, both `resolve()` outside the root and fail `is_relative_to`; a lexical prefix check passes both. APFS is case-insensitive but `resolve()` keeps the typed case, and `os.path.normcase` is the identity on POSIX (audit probe: an upper-cased root fails `is_relative_to` even through `normcase`). So POSIX comparison is strict and a mis-cased `allowed_dirs` entry refuses rather than admits; `normcase` is applied on Windows only, where it folds case.
- **What `open` launches (this Mac, `NSWorkspace` default-handler probe, nothing executed):** a no-extension file with the exec bit → Ghostty/Terminal; `.command`/`.terminal` → Terminal; `.jar` → JavaLauncher; `.workflow` → Automator Installer; `.pkg` → Installer; `.dmg` → DiskImageMounter; `.webloc` → Safari; `.scpt` → Script Editor (a run-only applet is [unverified]). A denylist is therefore best-effort; only an allowlist of document types makes "never runs a program" true.
- **Spotlight injection probe:** `mdfind 'kMDItemFSName == "*a"b*"c'` with an unescaped user quote truncated the predicate at the quote and matched as `*a` (13 rows, identical to `kMDItemFSName == "*a"`); escaping `\` → `\\` and `"` → `\"` inside the predicate matched only the target. `-name` passes the term as argv (no predicate parsing) but cannot be OR'd with a content match, so p2 builds one escaped predicate.
- **`mdfind` facts verified live on this Mac:** multiple `-onlyin` flags are accepted; `$time.now(-N)` is a valid modification-date bound; `kMDItemContentTypeTree` matches `com.adobe.pdf`, `public.image`, `public.source-code`, `public.composite-content` (PDF, Office) and `public.text`; `-0` NUL-separates output. `~/Library/Messages` and `~/Library/Safari` return nothing, but `~/.ssh` (2 rows), `~/Library/Mail` (26,473 rows) and `~/Library/Application Support/{Google,Microsoft Edge}` do, so p2's exclusion of `~/Library` and every hidden component by prefix is load-bearing, not belt-and-braces.
- **Persistence, stated honestly:** `find_files` carries no `reads_desktop_content` marker, so in `/agent` its arguments and the first 240 chars of its result persist through `Agent._trace` (`agent.py:155`) into the log file and `chat_log`; in chat, tool results stay in the local `messages` list (`orchestrator.py:1692`) but the assistant's reply quoting paths reaches `ConversationSession.history` and so the conversation summary, and `log.debug` at `:1759` logs args plus 200 chars. Operator accepted this on 2026-09-04: file names are treated like app names, contents never return.
- **`SENSITIVE_APPS` false-positives on filenames** (`health`, `calm`, `messages`, `signal`, `chase`, `keeper`, `fidelity`, `keychain`); `contains_sensitive_content_term` (`tokenpal/brain/personality.py:293`) is the narrower list the repo already uses for untrusted content and drops `keychain`/`chase`/`fidelity`. p1's `path_is_sensitive` shipped WIDER than planned (operator, 2026-09-04): the narrow list plus `_SENSITIVE_PATH_TERMS` and `_SENSITIVE_EXTS`. See the p1 shard.
- **No guarded walk exists in the repo** (`grep -rn "os.walk\|rglob\|scandir" tokenpal/` → only `tokenpal/tools/remote_train.py:1849`, an unbounded `rglob`; `filesystem_pulse` uses watchdog events). `~/Downloads` here holds 1,837 files, max depth 9; `fd` is not installed.
- **Windows Search (Microsoft Learn, cited in p4):** provider `Search.CollatorDSO`, `SCOPE='file:C:/...'` for deep scope, `System.FileName LIKE`, `CONTAINS`, `System.DateModified >= DATEADD(DAY, -N, GETGMTDATE())`, `System.Kind`, `SELECT TOP n ... ORDER BY System.DateModified DESC`; string literals single-quoted with `'` doubled. `pythoncom.CoInitialize()` per worker thread. `os.startfile` runs anything with an association and raises `OSError`. pywin32 import guard precedent: `tokenpal/senses/app_awareness/win32_apps.py:15-20`.
- **Test conventions:** `tests/test_actions/test_open_app.py:33-47` patches `tokenpal.actions.open_app.subprocess.Popen`; `current_platform` is `lru_cache`d (`tokenpal/util/platform.py:9-14`), so tests patch the name in the action module. `tests/test_actions/test_catalog.py:17-27` pins the `LOCAL_SECTION` name set. `tests/_helpers.py:180` `allow_confirm`; `tests/test_brain/test_tool_loop.py:79-96` `_make_brain(agent_bridge=...)` and `:397-398` asserting the tool message sent back to the LLM. The existing `_StubAction`/`_FailAction` there (`:20`, `:34`) inherit `requires_confirm = True` from `AbstractAction` (`base.py:67`), so p3's gate would refuse them until they declare `False`.

## Done criteria
- On this Mac, in `/agent`: "find the tax pdf I downloaded this week and open it" runs `find_files`, then a confirm modal shows the resolved path under `~/Downloads`, Yes opens it in Preview, No returns "User denied open_path." to the model.
- In plain chat on this Mac: "open <a file find_files just listed>" shows the same modal; "open Calculator" (the `open_app` tool) now shows a modal too.
- A temp tree containing `.ssh/id_rsa`, `x.env`, `credentials.json`, `1password-export.csv`, a hidden dir, and a `Library/` dir under an allowed root yields none of them from `find_files` (walk backend) and `open_path` refuses each (a `signal-*` or `health-*` filename is deliberately allowed: the narrow term list treats those as ordinary words).
- `open_path` refuses a path outside `allowed_dirs`, a directory, a symlink resolving outside, and every executable/script case in p3's list, each with a message naming the reason.
- `ruff check tokenpal/` clean, `mypy tokenpal/ --ignore-missing-imports` zero errors, `pytest` green.

## Parking lot
- ~~ADJACENT: `grep_codebase` duplicated per-file-cap flag + `>=` off-by-one~~ → **admitted into p2 by operator 2026-09-04**; see the p2 shard's Work.
- ADJACENT (p1 review): `REJECT_PATH` is unanchored for `.env`/`credentials`/`secrets` but end-anchored for `.key`/`.pem`, so under `~/Documents` it false-positives on `Trade Secrets (novel).pdf` and `My Credentials CV.pdf` and blanks their subtree from `find_files` with no explanation. Decide in p2.
- OPEN QUESTION (p1 review, deferred by the dispatching session): `[paths] allowed_dirs` and `filesystem_pulse.roots` are two user-editable folder lists with the same three defaults (`tokenpal/config/paths.py:28` `default_watch_roots()`). Should `/watch add ~/Projects` make `find_files` see that folder? Kept separate for now — merging couples a watchdog noise knob to a security boundary.
- ADJACENT: `read_file.py:75` applies the broad `contains_sensitive_term` to paths (same false positives). Out of scope: `read_file` policy is a non-goal.
- ADJACENT: `app.py:228-231` never passes `action_configs`, so `memory_query`'s `data_dir` config is dead. Not needed here; tools read config at execute time.
- ADJACENT: `open_app` on Linux is unsupported (`open_app.py:90`); `open_path` gets `xdg-open`, `open_app` does not. Separate small change if wanted.
- ADJACENT: #60 duplicate `register_action` overwrite. Avoided by design here, not fixed.
