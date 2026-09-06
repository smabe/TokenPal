# harness-tool-policy-p3 — Path policy declared, enforced in the invoker

You are phase `p3` of the `harness-tool-policy` plan. This phase delivers, as one commit, `path_params` / `path_roots` / `path_screen` on `AbstractAction`, resolution and containment inside `ToolInvoker.invoke`, and the deletion of the hand-written containment in `read_file`, `grep_codebase` and `open_path`.

## Inherited from p2 — verify before you start
**p2 is a gate for this phase.** It routed `Brain._execute_tool_call` (`orchestrator.py:1942`) and `_handle_followup` (`:2635`) through `ToolInvoker.invoke`. This phase deletes containment from three tools' `execute()` bodies and puts it in `invoke` — so if p2 has NOT shipped, or has been reverted, that deletion leaves the chat and ambient paths with no containment, no screen and no root at all, and every path those tools refuse today becomes an accept. Two independent auditors read this shard in isolation and reached exactly that conclusion, so confirm it yourself before editing anything:

```
grep -rn "action\.execute(" tokenpal/brain/     # must return nothing
```

If it returns a hit, stop and report — do not proceed with the deletions in Work.

## Locked decisions
See the master `plans/harness-tool-policy.md`. The decisions binding this phase:
- **`path_screen` governs the RAW-name screen ONLY. The resolved-name screen is always `path_is_sensitive(rel)`.** `"narrow"` means no raw screen at all (today's `open_path` and `find_files`); `"broad"` means `REJECT_PATH` plus `contains_sensitive_term` on the raw name (today's `read_file`, `read_file.py:64,67`).
  Two measurements force this shape. The predicates are not nested in either direction — `contains_sensitive_term` is **False** for `docs/credentials.md`, `.env`, `a.pem`, `sub/id_rsa`, all of which `path_is_sensitive` catches, while `path_is_sensitive` is False for `chase`, `fidelity`, `health`, `calm`, `messages`, `signal`, `headspace` — so an enum SELECTING one predicate for both stages would make `read_file` return `docs/credentials.md`, undoing `087d5a4`. But screening the raw name unconditionally is also wrong: verified, `path_is_sensitive("/Users/me/Documents/credentials-app/README.md")` and `REJECT_PATH` both fire on the raw absolute path, while `open_path` today sees only `rel = "README.md"` (False). Any raw screen is therefore a NEW refusal for `open_path` and `find_files`, not a superset.
  Result per tool: `read_file` broad + resolved — exactly today. `grep_codebase` broad + resolved — today's raw `contains_sensitive_term` plus `REJECT_PATH` and a resolved screen it has never had, both strengthenings. `open_path` narrow + resolved — exactly today.
- **`grep_codebase` declares `path_screen = "broad"`.** It applies `contains_sensitive_term` to the raw argument today (`grep_codebase.py:60`); declaring narrow would drop `chase`, `health`, `messages`, `signal`, `headspace`. Five auditors flagged the draft's `"narrow"` independently.
- **The relative-path anchor follows the roots policy.** `resolve_inside` resolves relatives against process cwd, so the anchor must be explicit or it silently changes meaning. `git_root` policy joins `root / raw` before resolving — preserving `read_file.py:74` and `grep_codebase.py:64`, and honouring `read_file`'s own schema text "Relative paths resolve against the git root" (`read_file.py:51`). `allowed_dirs` policy passes `raw` through cwd-anchored, preserving `open_path.py:85`; with N roots there is no single anchor to join to, which is why that tool works this way today. Every existing test `chdir`s to the root, so nothing in the suite would have caught a reanchor.
- **A `path_roots = "git_root"` tool refuses when there is no git root, in BOTH call shapes.** `read_file.py:69-71` refuses today; `grep_codebase.py:54` falls back to `Path.cwd()`. The shared resolver adopts `read_file`'s behavior, and `grep_codebase` adopts it for its no-path default too. Note `path` is OPTIONAL on `grep_codebase` (`:41`, `required: ["pattern"]`), so the invoker resolves nothing on a bare-pattern call and the tool keeps its own `git_root` call for that shape — it must NOT also call `git_root` when a path was given, or the phase adds a second `git rev-parse` per call instead of relocating one. This is a deliberate behavior change for `grep_codebase` in a non-git cwd, recorded rather than slipped in: "Search the current repo" has no meaning outside one.
- **No `git_root` memoization.** Four auditors flagged the drafted memo: `functools.cache` on an `async def` caches the coroutine and the second await raises `RuntimeError: cannot reuse already awaited coroutine` (reproduced); a process-lifetime cache defeats the `monkeypatch.setattr("tokenpal.util.paths.git_root", ...)` at `tests/_helpers.py:244` and `tests/test_util/test_paths.py:90,104,147,160,230`; and **25 `monkeypatch.chdir` calls** across `test_read_file.py`, `test_git_actions.py` and `test_grep_codebase.py` drive exactly these tools, so one test would read another's root. The subprocess is already paid inside every path tool today, so parity needs no cache.
- **`grep_codebase` uses the git root**, matching its own description "Search the current repo" (`grep_codebase.py:23-25`). Operator sign-off 2026-09-05.
- **The invoker substitutes a `ResolvedPath` NamedTuple into a COPY of `kwargs`, never a bare string.** It carries `raw`, `resolved`, `root`, and `rel` — `rel` as a posix string, since `read_file.py:78` feeds it to a git pathspec. Four reasons, each from an auditor: a bare resolved string makes `read_file`'s `_spelled_rel(path_arg, root)` return exactly `rel`, so `spelled != rel` (`read_file.py:91`) is never true and the untracked-symlink defence dies; `open_path` keeps `is_hidden_or_protected(resolved, root)` and has no other source of `root` once `resolve_inside` is deleted; `read_file` needs `root` for `git -C`; and substituting in place would put resolved absolute paths into the DEBUG log at `orchestrator.py:1944`, which formats `tc.arguments` after `execute`.
- **Every converted tool's `isinstance(path_arg, str)` guard must be updated.** `grep_codebase.py:59` gates on it and falls through to `target = root` (`:65`) — a substituted non-str would silently discard the validated target and search the whole repo. `read_file.py:60` and `open_path.py:78` fail loudly instead. All three guards change with the substitution.
- **Substitution happens after the confirm, which is where it already effectively is.** The confirm modal renders raw arguments (`app.py:270-294`) and `open_path` resolves afterward, so this preserves today's ordering exactly. Showing the resolved path at the prompt would be an improvement and is explicitly NOT in this phase.
- **Containment runs before the rate-limit block, inside `invoke`.** Containment awaits (`git_root` subprocess); the rate-limit check-and-append must stay await-free or `gather` lets a whole round through — demonstrated, `[True, True, False, False, False]` becomes `[True, True, True, True, True]`.
- **`find_files` keeps its output-side filter.** It declares no path argument (`find_files.py:257-283`, the `parameters` schema), so `path_params` is structurally inapplicable. Only the roots computation is shared.
- **`read_file` keeps its git-tracking check.** Both spellings must be tracked (`read_file.py:88-92`); that is a membership predicate no roots declaration expresses.

## Work
- Scope trace: DIRECT — the master's Goal names closing #74 structurally, and this phase is the input-side half of it.
- `tokenpal/actions/base.py` — three ClassVars. Proposed (shape is contract; names, defaults and the literal spellings are proposals):
  ```python
  # Argument names carrying a filesystem path. Empty means the invoker does no
  # path work for this tool at all — the gate that keeps a git subprocess off
  # every non-path tool call.
  path_params: ClassVar[tuple[str, ...]] = ()
  path_roots: ClassVar[Literal["git_root", "allowed_dirs"]] = "allowed_dirs"
  # "broad" = contains_sensitive_term (read_file's screen today);
  # "narrow" = path_is_sensitive (find_files / open_path today).
  path_screen: ClassVar[Literal["broad", "narrow"]] = "narrow"
  ```
- `tokenpal/util/paths.py` — a shared resolver the invoker calls. **No memoization of `git_root` and no cache of `load_config()`** — see the Locked decisions; the four reasons are the coroutine-reuse error, two monkeypatch surfaces, and `/options` rewriting `config.toml` at runtime. Proposed shape:
  ```python
  class ResolvedPath(NamedTuple):
      raw: str
      resolved: Path
      root: Path
      rel: str

  async def resolve_declared_path(raw: str, roots_policy: str, screen: str
                                  ) -> tuple[ResolvedPath | None, str]:
      """Return (path, "") or (None, refusal). Order is fixed:
      raw-name screen → resolve_inside → resolved-name re-screen."""
  ```
  The error case is NOT a bare `str` in a union with the success tuple: verified, `a, b = "no"` unpacks successfully, so a caller that forgets an `isinstance` would bind a refusal's characters as a path and mypy would not flag it.
  The two-screen order is load-bearing: collapsing it to one loses the symlink-laundering defence added in `087d5a4`.
- `tokenpal/actions/invoker.py` — before the rate-limit block, for each name in `action.path_params` present in `arguments`, resolve and either refuse (returning a failed `ActionResult`) or substitute the resolved value. Skip entirely when `path_params` is empty.
- `tokenpal/actions/read_file.py` — declares `path_params = ("path",)`, `path_roots = "git_root"`, `path_screen = "broad"`. Deletes `REJECT_PATH.search` (`:64`), `contains_sensitive_term` (`:67`), the `git_root` call (`:70`), `resolve_inside` (`:74`) and `path_is_sensitive` (`:82`). **Keeps** `_spelled_rel` and the dual `git ls-files` check (`:87-92`), reading `path.raw` for the spelled name and `path.root` for `git -C`. It makes no `git_root` call of its own.
- `tokenpal/actions/grep_codebase.py` — declares `path_params = ("path",)`, `path_roots = "git_root"`, `path_screen = "broad"`. Substitutes `str(path.resolved)` into the `rg` argv at `:75` (`:74` is the `pattern` argument) — validating without substituting leaves `rg` walking the raw target.
- `tokenpal/actions/open_path.py` — declares `path_params = ("path",)`, `path_roots = "allowed_dirs"`, `path_screen = "narrow"`. Deletes `allowed_roots` (`:81`) and `resolve_inside` (`:85`). **Keeps** the resolved-target checks: `exists`/`is_dir`/`is_file` (`:93-98`), `is_hidden_or_protected` (`:100`), `_DENIED` suffix, `os.access(X_OK)` and `_inside_bundle` (`:103-108`) — all consume the resolved `Path`, which the invoker now supplies.
- `tokenpal/actions/find_files.py` — no `path_params`. Uses the shared roots computation only; `_post_filter` (`:211-242`) is untouched.
- `tests/_helpers.py` — `stub_allowed_root` (`:231`) monkeypatches `load_config` **on the action module**. When that call moves, the helper must patch the new owner. Every caller (`tests/test_actions/test_find_files.py:83`, `tests/test_actions/test_open_path.py:57`, and the direct `monkeypatch.setattr(open_path_mod, "load_config", ...)` at `test_open_path.py:142`) follows.
- `tests/test_actions/test_read_file.py` — `:102` asserts the refusal with `==` against `"Path is outside the git repo."`. A shared refusal string is a deliberate edit here, not an accident. The no-echo assertions (`:117-125`, `:166-178`) must still hold.
- `tests/test_actions/test_find_files.py` — follows `stub_allowed_root` to its new owner; `:83` is the caller.
- `tests/test_util/test_paths.py` — covers the shared resolver: both screens, the 3-tuple, the refusal shape, and the no-git-root refusal. Five existing tests monkeypatch `git_root` (`:90,104,147,160,230`) and must keep winning, which is why no memo is added.
- `tests/test_actions/test_grep_codebase.py` — gains the containment tests it has never had: absolute outside the repo, `..`, symlink escape.
- `tests/test_actions/test_open_path.py` — containment moves; the `_DENIED`/exec-bit/bundle tests and the no-echo assertion (`:230-239`) stay green unchanged.
- `tests/test_invoker.py` — resolution, substitution, refusal, and the skip when `path_params` is empty.

## Decisions & findings
### Decision: `path_params` empty is the performance gate  *(status: active)*
- **Rationale:** measured, `load_config()` is 0.64 ms and `allowed_roots` 3.52 ms, dominated by a `git rev-parse` subprocess, and neither caches. All four path tools already pay this inside `execute()`, so hoisting relocates the cost. Running it unconditionally would add a subprocess to all 39 tools on every call, including every idle roll at the 2 s tick.
- **Evidence:** `tokenpal/config/loader.py:141-172`; `tokenpal/util/paths.py:21-64`. Timings are the re-measured ones in the master (`load_config()` 0.73 ms, `git_root` 4.63 ms, `allowed_roots` 5.11 ms); the first-pass figures had `allowed_roots` cheaper than the `git_root` it awaits, which cannot be true.

### Decision: the raw-then-resolved double screen is preserved, not collapsed  *(status: active)*
- **Rationale:** `087d5a4` was written because screening only the raw argument lets a tracked symlink launder a denied filename — reproduced then as `docs/credentials.md` refused while `notes.md → docs/credentials.md` returned `AWS_SECRET=abc123`. One screen on either input alone reopens it.
- **Evidence:** `tokenpal/actions/read_file.py:64,67,82`; `tokenpal/actions/find_files.py:230`; `tokenpal/actions/open_path.py:100`.

## Failure modes to anticipate
- **Refusal strings must not become more distinguishable than they are today.** `open_path.py:100` merges `is_hidden_or_protected` and `path_is_sensitive` into one refusal ("That path is protected."). Splitting the screen into the invoker risks two distinct strings, which would let a caller tell "denied name" from "protected location" — a finer oracle than the tool exposes now. Keep one string for that pair.
- **`grep_codebase` fails open on an unexpected argument type.** `:59`'s `isinstance(path_arg, str)` falling through to `target = root` is the single most dangerous line in this phase: it turns a substitution mistake into a silent whole-repo search rather than an error.
- **`allowed_roots` has a surprising empty-list rule.** `[]` returns `[]` (disables the tool), but `[""]` and `["/nonexistent"]` both fall through and re-enable it with the cwd git root. A rewrite must reproduce that exactly, including the bare-string wrapping at `paths.py:46`.
- **`open_path` checks `exists()` before the sensitivity screen**, so it distinguishes a denied name that exists from one that does not. Adopting deny-before-stat for the containment step narrows that oracle; the remaining ordering inside `open_path` is parked, not this phase's to change.
- **The agent's in-run cache is consulted before `invoke`** (`agent.py:322-329`), so a cache hit bypasses containment entirely. Safe today — only `success=True` results are cached and a refusal is `success=False` — but the cache key is built from raw arguments (`_stable_args_key`, `:325`), so `foo` and `./foo` cache separately once the invoker canonicalizes. Record the behavior; do not redesign the cache.
- **Refusal strings must not echo the path.** The name itself can be the secret. `open_path._refuse` (`:46-47`) takes fixed strings only; keep that property in the shared layer.
- **Windows.** `resolve_inside` case-folds via `os.path.normcase`, which is identity on POSIX. Neither the folding nor `find_files`' Spotlight branch was exercised on a real Windows host this session.

## Done criteria
- `grep -n "resolve_inside" tokenpal/actions/read_file.py tokenpal/actions/grep_codebase.py tokenpal/actions/open_path.py` returns nothing.
- Observable: `open_path` still opens a benign file under a badly-named folder — `<allowed_root>/credentials-app/README.md` — proving the raw screen did not leak onto a narrow tool. Run it and paste the result.
- Observable: in a scratch repo holding a tracked `docs/credentials.md` and a tracked symlink `notes.md` pointing at it, `read_file` refuses both spellings and `grep_codebase` refuses an absolute path outside the repo — run all four calls and paste the outputs into this shard's findings.
- `open_path` still refuses a denied suffix, an executable bit, and an app-bundle ancestor, each on the resolved target.
- `find_files` with `allowed_dirs = []` is still disabled, and with `[""]` still resolves to the repo root.
- `read_file` still refuses an UNTRACKED symlink pointing at a tracked file — `tests/test_actions/test_read_file.py:155` is the existing test, and it must pass without modification. This is the assertion the substitution design nearly destroyed.
- `pytest tests/test_actions/ tests/test_invoker.py tests/test_util/test_paths.py` green; full suite green.
