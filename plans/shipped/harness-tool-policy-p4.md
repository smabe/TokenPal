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
- `tokenpal/util/paths.py` — **added to Work during execution.** The Work said to reuse `find_files._post_filter`'s predicate rather than write a second one, and named extraction as in scope, but did not say where the extracted helper lands. `is_screened_out(resolved, root, rel, screen)` lives here beside `resolve_declared_path`, whose output-side counterpart it is. Planning miss: an extraction has a destination and the Work should have named it.
- `tokenpal/actions/find_files.py` — **added to Work during execution.** Same cause: `_post_filter`'s inline pair becomes the extracted call. It passes the literal `"narrow"` rather than its own `path_screen`, because it declares no `path_params` and so inherits p3's fail-closed `"broad"` default, which would newly refuse benign names under the broad app terms.
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

### Finding: the Done-criteria observable, before and after  *(status: active)*
Scratch git repo holding `credentials.md`, `sub/id_rsa`, `we:ird.txt`, `ok.txt`, a
`.aws/credentials`, a gitignored `ignored/prod.env` and a seeded `.git/description`, each
containing `MARKERSECRET`. Both runs go through `ToolInvoker.invoke`; BEFORE is a detached
worktree at p3's `e8e2692`, AFTER is this phase's tree. Paths shown repo-relative.

```
                       BEFORE (e8e2692)                       AFTER
no path argument       credentials.md:1:MARKERSECRET=abc      we:ird.txt:1:MARKERSECRET colon
                       sub/id_rsa:1:MARKERSECRET=ghi          ok.txt:1:hello MARKERSECRET world
                       we:ird.txt:1:MARKERSECRET colon
                       ok.txt:1:hello MARKERSECRET world
path=".git"            .git/description:1:MARKERSECRET=xyz    No matches.
path=".aws"            .aws/credentials:1:MARKERSECRET=jkl    No matches.
path="ignored"         ignored/prod.env:1:TOKEN=MARKERSECRET  No matches.
path="ok.txt"          1:hello MARKERSECRET world             1:hello MARKERSECRET world
```
All three p3 rows are closed and the two Done-criteria files are gone; the control and the
single-file output shape are unchanged. A screened target answers "No matches." rather than a
refusal, so the tool does not report whether the folder held anything.

### Decision: `rg --null`, not `--json` and not a colon split  *(status: active)*
- **Rationale:** the path is only ambiguous because `:` separates it from the match. `--null`
  emits `path\0line:text`, so the split is unambiguous, the reconstructed output line is
  byte-identical to today's, and no JSON parser or output-shape change is needed. Verified on
  ripgrep 15.2.0 with a real `we:ird.txt`, which BOTH runs return intact.
- **Evidence:** `tokenpal/actions/grep_codebase.py` `_screened_hits`.

### Finding: rg prints NO path when the target is a single file  *(status: active)*
`rg --null ... <file>` emits `1:hello ...` with no path and no NUL — today's behavior too, so
the output shape is not this phase's to change. That hit belongs to the target, and screening it
against the target is load-bearing, not a fallback: `path=".git/description"` passes the
invoker's name screens (`path_is_sensitive(".git/description")` is False) and is refused only
here. Pinned by `test_a_single_file_target_keeps_its_hit_and_its_shape`.

### Finding: user-visible tightening beyond the two named files  *(status: active)*
The screen is `is_hidden_or_protected(resolved, root) or path_is_sensitive(rel)` plus, for the
declared `"broad"` strength, `contains_sensitive_term(rel)`. So `grep_codebase(path=".github")`
now returns nothing, where `read_file` deliberately still reads a tracked
`.github/workflows/*.yml` — which is exactly why `is_hidden_or_protected` stayed out of
`resolve_declared_path`. A repo file named `secrets.md` also stops being greppable. The default
no-path walk is unaffected: rg already skips hidden and ignored files there.

### Decision: `find_files._post_filter` calls the extracted predicate with a literal `"narrow"`  *(status: active)*
- **Rationale:** extraction had to be behavior-preserving, and `find_files` declares no
  `path_params`, so its inherited `path_screen` is the fail-closed `"broad"` default and reading
  it would newly refuse benign names under the broad app terms. The literal keeps `_post_filter`
  byte-equivalent while the predicate itself is now the parameterized one.
- **Evidence:** `tokenpal/util/paths.py::is_screened_out`; `tokenpal/actions/find_files.py:231`.

### Finding: the contract test's three fail-open modes, each demonstrated closed  *(status: active)*
- **Empty parametrization:** forcing `_DECLARING = []` gives `2 failed, 2 passed, 2 skipped` —
  the two anchors fail while the parametrized cases skip, so the run is red, not green.
- **Constructor raise:** `_instantiate` fails when `current_platform() in cls.platforms` and
  skips only for a genuinely unsupported host.
- **Unimportable module:** `test_every_action_module_imports` walks `tokenpal.actions` and
  collects failures as a finding, because `discover_actions` swallows `ImportError` and would
  otherwise silently shrink the registry the whole file selects on.
Mutation-checked: replacing `read_file`'s `isinstance(path, ResolvedPath)` guard with
`path is None` turns `test_a_declaring_tool_refuses_a_value_the_invoker_did_not_contain[read_file]`
red. The throwaway offender (a registered action with a `"path"` schema property and no
`path_params`) failed
`test_a_tool_with_a_path_shaped_argument_declares_a_path_policy` and was deleted.

### Finding: the schema selector has no false positives today  *(status: active)*
Across all 39 registered actions, the substring selector (`path`/`file`/`dir`/`folder`) matches
exactly three properties, all named `path`, all on the three declaring tools. `find_files`'
arguments (`query`, `kind`, `modified_within`, `limit`) are not path-shaped, as p3 recorded.

## Decisions & findings — shipped at `1094948`

### The first version of this phase did not close what it claimed. Three holes, all reproduced:
1. **`str.splitlines()` splits on eight bytes beyond `\n`** (`\v \f \r \x1c \x1d \x1e \x85 \u2028 \u2029`). One rg record became two Python lines; the tail carried no separator, fell back to the target's path and inherited the TARGET's verdict. A form feed in an ordinary source file leaked a screened file's content. Fixed with `split("\n")` plus a rule that a separator-less record is dropped unless the target is a single file.
2. **A filename containing a newline** left an orphan fragment that was reparsed as a path and resolved against the **cwd**, so a file inside a fully screened `.aws` came back. Fixed by requiring a record's path to start with `str(target)`.
3. **`git check-ignore` C-quotes** any path holding a non-ASCII byte, a quote, a backslash, a tab or a control character (`core.quotePath` defaults true), and a quoted path never matched ripgrep's raw one — so the ignore screen failed OPEN on `ignored/café.txt` and `ignored/qu"ote.txt` while correctly withholding `ignored/plain.txt`. `-z --stdin` makes git emit paths verbatim, and removes an ARG_MAX ceiling: measured, 5,000 paths raised `OSError [Errno 7]`, which the fail-closed handler turned into a silent `"No matches."`.

### The contract test passed with containment deleted from the invoker
Neutering `_contain_paths` to `return dict(arguments)` left it 10 passed: asserting only `success is False`, each tool still refused the raw `str` on its own type guard — the same verdict for a different reason. It now asserts `result.output == _OUTSIDE[cls.path_roots]` and adds a positive case that an in-root path arrives as a `ResolvedPath`. Re-run under the same mutation: **6 failed, 7 passed**.

### `rg` prints no path at all for a single-file target
Load-bearing, not cosmetic: `path=".git/description"` passes every invoker screen (`path_is_sensitive(".git/description")` is False), so its hits are refused only by attributing the pathless record back to the target. The benign single-file output shape (`1:text`, no prefix) is preserved.

### `find_files` reads its own ClassVars
It declared `path_roots`/`path_screen` while `_post_filter` and `execute` used string literals, so the declaration could drift from behaviour undetected — and `_DECLARING` in the contract test filters on `path_params`, which `find_files` does not have. `_post_filter` now takes the screen and `execute` passes `self.path_roots`.

### Verified `git check-ignore` semantics
rc 0 = some ignored, rc 1 = none, rc 128 = fatal. Works in a repo with no commits, in a linked worktree, and honours `core.excludesFile`. A path outside the repo gives rc 128 → fail closed. A tracked file matching an ignore pattern reports rc 1, which is correct.

### Cost
`check-ignore` costs 5.8-6.3 ms of a 43-45 ms call on this repo (509 distinct paths). `grep_codebase` is not ambient-eligible after p1 and appears in neither idle catalog, so it is reachable only from typed chat and `/agent`.

### Parked
A hardlink to a screened file bypasses the name-based screen by construction — verified. The model has no write tool, so this needs prior filesystem write. A single-file binary target returns rg's own `binary file matches` notice verbatim; in directory mode rg skips such files entirely.

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
