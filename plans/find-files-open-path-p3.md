# find-files-open-path-p3 — `open_path` action + confirm gate on the conversation path

You are phase `p3` of the `find-files-open-path` plan. This phase delivers, as one commit, the confirm-gated `open_path` tool and the confirm gate that plain chat lacks today, so `open_path` (and the existing `open_app`) prompt the user from chat exactly as they do from `/agent`. p1 shipped `tokenpal/util/paths.py`; p2 shipped `find_files`. Use them.

## Locked decisions
See the master `plans/find-files-open-path.md`. The decisions binding this phase:
- The confirm modal shows the tool name and raw arguments (`fmt_args`, `tokenpal/app.py:280-284`); there is no per-action pre-resolution hook (operator declined 2026-09-04). `open_path` re-validates at execute and refuses outside the allowlist, so a raw path that resolves elsewhere is refused after the prompt, never opened.
- The chat gate reuses `self._agent.confirm_callback` (`tokenpal/brain/orchestrator.py:410`, `AgentBridge:225-230`); when it is `None` the tool is refused, never executed (mirrors the agent path, `orchestrator.py:1857-1860`, and the headless deny at `app.py:291-294`).
- Confirms are serialized with an `asyncio.Lock`; the parallel `gather` at `orchestrator.py:1682-1684` stays.
- Denied text is `f"User denied {tc.name}."` (`tokenpal/brain/agent.py:326-330`).
- Openable-type policy (operator decision 2026-09-04, master Status): **denylist**. Any file opens with its default app unless its suffix is in `_DENIED`, it carries the executable bit, or it sits inside an app bundle. Opening a text file in an editor or an `.html` in a browser is wanted behavior.

## Work
- Scope trace: DIRECT — the requested outcome's second tool; the chat confirm gate is SAFETY (initial state: chat executes `requires_confirm` tools with no prompt → in-scope action: register `open_path` → failing outcome: the model opens files from chat unconfirmed).
- `tokenpal/actions/open_path.py` — new. Shape (proposal):
  ```python
  @register_action
  class OpenPathAction(AbstractAction):
      action_name = "open_path"
      description = ("Open a document, image, or media file from [paths] allowed_dirs "
                     "with its default app. Never runs programs; use open_app for apps.")
      parameters = {"path": str (required)}
      safe = False; requires_confirm = True; cacheable = False
  ```
  `execute` order, each refusal a one-line `ActionResult(success=False)` naming the reason and never naming a sensitive app:
  1. `roots = await allowed_roots(load_config().paths.allowed_dirs)`; empty → refuse naming `[paths] allowed_dirs`.
  2. `pair = resolve_inside(path, roots)`; `None` → "outside [paths] allowed_dirs"; else `resolved, root, rel = pair`.
  3. `not resolved.exists()` → "no such file"; `resolved.is_dir()` → "open_path opens files, not folders".
  4. `is_hidden_or_protected(resolved, root)` or `path_is_sensitive(rel)` → "path is protected". Use the `rel` from step 2; do not call `relative_to` again.
  5. type policy: suffix (lowercased) in `_DENIED` → refuse "open_path does not open scripts, programs, or installers". `_DENIED` = {.app .exe .sh .command .bat .ps1 .py .terminal .jar .workflow .pkg .mpkg .dmg .scpt .applescript .webloc .url .lnk .vbs .vbe .msi .msc .scr .cmd .com .pif .js .jse .wsf .wsh .hta .reg .rb .pl .php .zsh .fish}. Then `os.access(resolved, os.X_OK)` on a regular file → refuse "file is executable"; any ancestor component ending in `.app`, `.workflow`, `.action`, `.bundle` → refuse "inside an app bundle". Everything else opens.
  6. open: `darwin` → `subprocess.Popen(["open", str(resolved)], stdout/stderr DEVNULL)`; `windows` → `os.startfile(str(resolved))` (CPython docs name `NotImplementedError` when ShellExecute is unavailable; catch `OSError` as well for the launch failure path); `linux` → `Popen(["xdg-open", str(resolved)])`, `FileNotFoundError` → refuse "xdg-open not installed". Return `ActionResult(output=f"Opening {resolved.name}.")`.
- `tokenpal/brain/orchestrator.py` — in `Brain.__init__` add `self._confirm_lock = asyncio.Lock()` next to the other per-brain state; in `_execute_tool_call` (`:1747-1773`), after the `reads_desktop_content` refusal and before the `try`:
  ```python
  if action.requires_confirm:
      confirm = self._agent.confirm_callback
      if confirm is None:
          return f"Tool '{tc.name}' needs a confirmation prompt this overlay cannot show."
      async with self._confirm_lock:
          allowed = await confirm(tc.name, tc.arguments)
      if not allowed:
          return f"User denied {tc.name}."
  ```
- `tokenpal/app.py` — `_agent_confirm` (`:270-294`): title `"Agent confirmation"` → `"Confirm tool call"` and body first line `"Tool wants to run:"` unchanged. (Copy change; surfaced at approval.)
- `tokenpal/actions/catalog.py` — `LOCAL_SECTION` gains `CatalogEntry("open_path", "Open a document from [paths] allowed_dirs with its default app (asks first).", kind="local")`.
- `tests/test_actions/test_catalog.py` — add `"open_path"` to the pinned set.
- `tests/test_actions/test_open_path.py` — new; patch `tokenpal.actions.open_path.current_platform`, `tokenpal.actions.open_path.subprocess.Popen`, `os.startfile` (create the attribute with `monkeypatch.setattr(os, "startfile", fake, raising=False)`), `tokenpal.actions.open_path.load_config` (module-top import, as in p2), and `tokenpal.util.paths.git_root` → `None`:
  1. happy path on darwin: `root/a.pdf` → Popen called with `["open", str(resolved)]`, output `"Opening a.pdf."`.
  2. outside root, symlink resolving outside, `..` escape → refused, Popen not called.
  3. directory, missing file → refused.
  4. each denied case: `run.sh`, `run.command`, `x.app/Contents/Resources/icon.png` (a benign suffix, so the bundle-ancestor check is what refuses it), `tool.py`, `setup.exe`, `a.lnk`, `b.vbs`, `c.jar`, `d.pkg`, `e.dmg`, `f.webloc`, a no-extension file with mode 0o755, `notes.txt` with mode 0o755 → refused; `notes.txt` at 0o644, `page.html`, `README` (no suffix, 0o644), `notes.unknownext` → opened.
  5. windows branch: `os.startfile` called with the resolved path; `OSError` → refusal.
  6. sensitive/protected: `root/1password-export.pdf` → refused without the word "1password" in the output; `root/.hidden/a.pdf`, `root/Library/a.pdf`, `root/x.env`, `root/credentials.json` → each refused, Popen not called.
- `tests/test_brain/test_tool_loop.py` — first add `requires_confirm = False` to the existing `_StubAction` (`:20`) and `_FailAction` (`:34`): they inherit `True` from `AbstractAction` (`tokenpal/actions/base.py:67`) and run without a bridge, so the new gate would refuse them. Grep `tests/test_brain/test_conversation.py`, `test_reply_continuation.py`, `test_news_dispatch.py` for other stub actions that reach `generate_with_tools` and do the same. Then, using `_make_brain(agent_bridge=...)` (`:79-96`) and the tool-message assertion pattern (`:397-398`):
  1. a fake `requires_confirm=True` action + `allow_confirm` (`tests/_helpers.py:180`) → executed once, result returned.
  2. a denying callback → `execute` not called, tool message `"User denied <name>."`.
  3. `confirm_callback=None` → not executed, message names the missing prompt.
  4. two gated calls in one round with a callback that records enter/exit timestamps and sleeps → intervals do not overlap.
  5. `requires_confirm=False` action → callback never invoked.
- `docs/claude/actions.md` — under the built-ins bullet list: one bullet for `find_files`/`open_path` naming `[paths] allowed_dirs`, the denylist policy, and that both persist arguments and paths like any unmarked tool; one bullet stating the conversation path now confirms `requires_confirm` tools through the agent bridge's callback, serialized, and refuses them when no callback is wired.
- `CLAUDE.md` — Privacy section: one line: "`find_files`/`open_path` (#53) are confined to `[paths] allowed_dirs` plus the current git root; they return and persist file paths, never contents; `open_path` opens documents, images, and media only and asks first."

## Decisions & findings
### Decision: denylist for openable types  *(status: active; operator-chosen 2026-09-04)*
- **Rationale:** operator: "no scripts .sh .jar etc. — opening a text editing app or a web browser is useful." The denylist is the issue's list extended by the security probe on this Mac (`open` launched Terminal for a no-extension exec-bit file and `.command`, JavaLauncher for `.jar`, Installer for `.pkg`, Automator for `.workflow`) and by Windows `PATHEXT` (`.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC` [unverified: no authoritative page fetched this session]). The exec-bit and bundle-ancestor checks cover files the list cannot name.
- **Alternatives considered:** an allowlist of document/image/media suffixes — rejected by the operator as too restrictive for editors and browsers.
- **Evidence:** master Background findings (`open` handler probe; Windows `PATHEXT`).

### Decision: lock, not sequential execution  *(status: active)*
- **Rationale:** the parallel `gather` benefits network tools; only the modal must be exclusive. A lock is three lines and changes nothing for ungated tools.
- **Evidence:** `orchestrator.py:1682-1684`; `agent.py:222-224`; `ui/qt/overlay.py:783-789`.

## Failure modes to anticipate
- `asyncio.Lock()` created in `__init__` before the loop runs: fine on Python 3.10+ (binds lazily); do not create it inside `start()` where tests that call `_execute_tool_call` directly would miss it.
- The confirm future is resolved via `loop.call_soon_threadsafe` from the UI thread (`app.py:275-278`); the chat path awaits on the same brain loop — same as `/agent`, no new thread concern.
- `os.access(..., os.X_OK)` is True for every file on some Windows/FAT mounts; keep it; the suffix denylist and bundle check are the primary checks there.
- `Path.suffix` of `archive.tar.gz` is `.gz`; not openable, fine. `README` (no suffix) → not openable, fine.
- A raw path with `~` or relative form in the confirm modal reads oddly; acceptable per the locked decision, and the modal still shows what the model asked for.

## Done criteria
- All tests above run and pass; `pytest` green; `ruff` and `mypy` clean.
- Live on this Mac (`./run.sh --overlay textual`, `find_files` and `open_path` enabled in the `/tools` picker): `/agent find the tax pdf I downloaded this week and open it` → `find_files` result in the trace, a modal titled "Confirm tool call" showing `open_path(path=...)`, Yes opens the file in Preview; a second run answered No shows "User denied open_path." in the trace.
- In plain chat: "open <that file>" shows the same modal; "open Calculator" shows a modal for `open_app` (it did not before this phase).
- A temp tree with the executable cases above yields refusals for every one; `open` is never spawned in the test run (assert the Popen mock's call list).
