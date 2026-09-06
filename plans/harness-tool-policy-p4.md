# harness-tool-policy-p4 — Close #74's second half, then fail closed

You are phase `p4` of the `harness-tool-policy` plan. This phase delivers, as one commit, a per-hit output screen for `grep_codebase`, a contract test that fails any tool declaring a path parameter without a policy, and the author docs.

## Inherited from p1-p3 (shipped `3bbc46d`, `38d9d68`, `e8e2692`)
`AbstractAction` now declares `allow_unprompted`, `writes_durable_sink`, `path_params`, `path_roots`, `path_screen`. `ToolInvoker.invoke` is the only dispatch point and resolves every declared path into a `ResolvedPath` (`raw`, `resolved`, `root`, `rel`) before the tool runs. `read_file`, `grep_codebase` and `open_path` hold no containment of their own. `grep_codebase` already refuses an out-of-repo target — this phase closes what remains INSIDE an allowed target, which is a different mechanism.

## Locked decisions
See the master `plans/harness-tool-policy.md`. The decisions binding this phase:
- **Containment alone does not close #74.** Operator sign-off 2026-09-05 to include the output-side screen. Reproduced on current `main`: with **no `path` argument at all**, `grep_codebase` returns `<repo>/credentials.md:1:SECRETVAL=def` and `<repo>/sub/id_rsa:1:SECRETVAL=ghi`. Both filenames are refused outright by `read_file`. ripgrep's default hidden-file skip catches `.env` and nothing else.
- **The screen is on the filename, not the matched text.** Content-based secret detection is a non-goal of this plan and is parked.
- **The contract test must not inherit `test_privacy_contract.py`'s fail-open modes.** Three are known: `pytest.skip` on any constructor raise (`:242-246`), `_imported_modules` returning an empty set on an unimportable or source-less module (`:71-80`), and — found this session, not in #65 — an empty `parametrize` list being a **skip, not a failure**, demonstrated with a body of `assert False` collecting as `1 skipped`.

## Work
- Scope trace: DIRECT — closing #74 is a named outcome, and the master's Done criteria require an in-repo `credentials.md` to stop being returned. The contract test is PREREQUISITE for the plan's "safe by default" claim, which is otherwise unenforced.
- `tokenpal/actions/grep_codebase.py` — screen each hit's filename before returning it, using the tool's declared `path_screen` strength rather than a hardcoded predicate, **and apply `is_hidden_or_protected(resolved, root)` to each hit as well**. p3 measured that ripgrep's hidden-file and gitignore filters do not apply to a path named explicitly on the command line: `path=".git"` returned `.git/description:1:MARKERSECRET`, `path=".aws"` returned `aws_secret_access_key = …`, and a gitignored `ignored/` returned `prod.env:1:TOKEN=…`. A filename screen alone closes the second and third but not the first. `is_hidden_or_protected` belongs here and NOT in `resolve_declared_path`, because `read_file` must stay able to read a tracked `.github/workflows/*.yml`. `find_files._post_filter` is fixed-narrow today, so reuse means extracting its screening step into something the strength can parameterize — extracting is in scope, rewriting it is not. `rg` emits `path:line:text`; split the path and drop the hit when the tool's declared screen rejects it. Structurally the same job as `find_files._post_filter` (`find_files.py:211-242`) — read that first and reuse the predicate rather than writing a second one. Preserve the existing per-line `contains_sensitive_term(ln)` filter (`:89`) and the `_MAX_MATCHES` cap.
- `tests/test_actions/test_path_policy_contract.py` — new. The fail-closed contract, built on `test_privacy_contract.py`'s reusable techniques and differing on its three fail-open modes:
  - **Inverse selector, the technique from `test_privacy_contract.py:371-385`:** every registered action whose `parameters` schema declares a property whose name CONTAINS `path`, `file`, `dir` or `folder` (substring, not exact match — an exact match misses `file_path`, `target_dir` and friends) must name it in `path_params`. Selecting *on* `path_params` would reproduce the "forgot the declaration → invisible to the test" hole.
  - **A non-parametrized anchor**, as at `:249-255`: `importlib.import_module` the three known path tools by name and assert they are registered and declare a policy, so an import failure fails rather than empties the parametrization.
  - **Fail, not skip, on a constructor raise** when `current_platform() in cls.platforms`.
  - **Treat an unreadable or unimportable module as a finding**, not an empty set.
  - **Every tool declaring `path_params` must refuse a non-`ResolvedPath`.** p3 left a real gap here: the invoker skips containment when the declared argument is absent, blank, or not a `str`, and hands the raw value to `execute`. All three current tools defend, but a reviewer built a `Forgetful` tool that declares `path_params`, trusts the invoker, and received `{"path": ["/…/outside.txt"]}` uncontained. Assert the defence for every declaring tool, mechanically.
- **Behavioural, routed through `ToolInvoker.invoke` and NOT through `execute()`:** p3 removes containment from `execute()`, so calling it directly post-p3 would find `grep_codebase` happily returning matches for `/etc` and `open_path` reaching `_launch`. For every tool with a declared path param, invoke it with an absolute path outside all roots, assert `success is False` and that the refusal does not contain the path. Mechanical, no per-tool knowledge, so it survives a tool being added.
  - **Registry-wide invariant naming no tool**, the shape at `:332` and `:377-385`: `assert sorted(offenders) == []` with a message saying what to fix. (Not `:328`, a generator's `name` line, and not `:337`, which is a docstring inside a parametrized test — the opposite shape.)
- `CLAUDE.md` — correct the `allowed_dirs` claim: "an explicitly empty list disables them" holds for `[]` only; `[""]` and `["/nonexistent"]` both re-enable the tools with the repo root (`paths.py:39-64`). Add the author rule: a tool taking a filesystem path declares `path_params`/`path_roots`/`path_screen` and writes no containment of its own.
- `docs/claude/actions.md` — **`path_params`, `path_roots` and `path_screen` appear nowhere in it today** (grepped at p3), while the `resolve_inside` call that used to remind an author is deleted from all three tools, so the next filesystem tool written against this checklist ships with no containment. Document all three. p1 already rewrote this bullet's `allow_unprompted` / `writes_durable_sink` sentences at `3bbc46d`; extend rather than replace. The author checklist for the declared policy, beside the existing `reads_desktop_content` contract.

## Decisions & findings
### Decision: reuse `find_files._post_filter`'s predicate rather than write a second screen  *(status: active)*
- **Rationale:** the repo already has one output-side path screen, and this plan exists because the same job was written by hand in several places. A second implementation here would be the exact failure the plan is fixing.
- **Evidence:** `tokenpal/actions/find_files.py:211-242`; the master's Goal.

### Decision: the contract test selects on the schema, not on the declaration  *(status: active)*
- **Rationale:** a test that enumerates tools declaring `path_params` can only check tools that already remembered to declare it. Selecting on the JSON Schema property name catches the tool that forgot — which is the entire point of failing closed.
- **Evidence:** `tests/test_desktop/test_privacy_contract.py:371-385` uses this inversion for the desktop marker and is the local precedent. Verified this session: only three registered actions declare a path-shaped schema property, all keyed `"path"` — `read_file`, `grep_codebase`, `open_path`.

## Failure modes to anticipate
- **`rg` output parsing.** Splitting `path:line:text` on `:` is wrong for a path containing a colon. Use the leading-path form `rg` actually emits and split from the left on the known prefix, or pass a machine-readable flag; the worker records which and why.
- **The screen will drop legitimate hits.** A repo with a file named `secrets.md` will stop being greppable. That is the intent, but it is a user-visible behavior change and belongs in the commit message.
- **`find_files._post_filter` operates on resolved `Path`s from a backend**, not on strings from a subprocess. Reusing the predicate may mean extracting it; extracting is in scope, rewriting it is not.
- **An empty parametrize list passes.** If the new test's selector returns nothing, it must fail. Assert the selector is non-empty as its own test.
- **`test_privacy_contract.py` must stay green and unchanged.** This phase adds a sibling contract; it does not edit the desktop one. Its known fail-open modes are #65's to fix.

## Done criteria
- Observable: in a scratch repo holding `credentials.md` and `sub/id_rsa`, `grep_codebase` with a pattern matching both and **no path argument** returns neither — run it and paste the before/after output into this shard's findings.
- A throwaway action declaring `parameters` with a `"path"` property and no `path_params` fails `tests/test_actions/test_path_policy_contract.py`. Prove it by writing it, running it red, and deleting it.
- The new test file's selector is asserted non-empty, so an emptied parametrization fails rather than skips.
- `CLAUDE.md` no longer claims an empty `allowed_dirs` list is the only disabling case.
- `pytest tests/test_actions/ tests/test_desktop/test_privacy_contract.py` green; full suite green.
