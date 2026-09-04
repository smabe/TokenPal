# find-files-open-path-p2 — `find_files` action (Spotlight on macOS, bounded walk elsewhere)

You are phase `p2` of the `find-files-open-path` plan. This phase delivers, as one commit, the read-only `find_files` tool: an opt-in `local` action that searches the user's allowed roots by name (and, on macOS, by indexed content) and returns paths with modified times, newest first, never file contents. p1 has shipped `tokenpal/util/paths.py` and `[paths] allowed_dirs`; use them, do not re-implement them.

## Locked decisions
See the master `plans/find-files-open-path.md`. The decisions binding this phase:
- One class, `platforms` default (all three). All backend selection happens in one module-level coroutine, proposed name `_run_backend(plat: str, roots: list[Path], query: str, kind: str, since_s: int | None, limit: int) -> list[Path]`, called once from `execute` (the `tokenpal/actions/open_app.py:77-94` branching shape, lifted out so p4 can add a `windows` branch without touching `execute`). macOS → `mdfind`; anything else → the bounded walk.
- `from tokenpal.config.loader import load_config` and `from tokenpal.util.paths import allowed_roots, git_root, ...` at module top (not inside functions) so tests can patch `tokenpal.actions.find_files.load_config`; `git_root` is patched where `allowed_roots` calls it, `tokenpal.util.paths.git_root`.
- The Spotlight query is one escaped predicate (escape `\` → `\\`, `"` → `\"`), not `-name`, because name and content matches must be OR'd. Master Background findings record the injection probe.
- Every result, from either backend, passes through the same post-filter: `resolve_inside` (roots) → `(resolved, root, rel)`, `is_hidden_or_protected(resolved, root)`, `path_is_sensitive(rel)` — take `rel` from the match, never re-derive it with `relative_to`, then stat, sort by mtime desc, cap.
- The tool has no `reads_desktop_content` marker: it returns paths, not content, and its arguments/results persist like any other tool's (operator accepted; master Background findings).
- `limit` default 20, max 50, min 1; `modified_within` accepts `<int><h|d|w>`; `kind` ∈ `any|document|image|code|pdf`.

## Work
- Scope trace: DIRECT — the requested outcome's first tool.
- Scope trace: ADJACENT, **admitted by explicit operator expansion 2026-09-04** (found at the p1 review, in a file p1 touched). Not required by `find_files`; it rides here because it is the same `rg` surface.
- `tokenpal/actions/grep_codebase.py` — fix two defects in `execute`:
  - the argv builds both `--max-count=5` (`:68-71`) and `-m str(_MAX_MATCHES)`; they are the same ripgrep option, the later wins, so the per-file cap is 100, not 5, and one noisy file (a lockfile, a generated module) consumes the whole 100-line budget and starves every other file out. Demonstrated with real `rg` 15.2.0: same argv order as the action builds → 50 lines from one file; with the flags swapped → 5. Keep the intended per-file cap of 5 and the total cap of 100; do not simply delete one flag without deciding which cap each is meant to express.
  - `:94` appends `"... [capped at 100 matches]"` under `if len(kept) >= _MAX_MATCHES`, so it claims truncation when exactly 100 lines survived and nothing was dropped. Should be `>`.
  - Add regression tests to the existing `tests/test_actions/test_grep_codebase.py` covering both: the argv contains exactly one per-file-cap flag with the intended value, and a result of exactly `_MAX_MATCHES` lines carries no "capped" suffix.
- Scope trace: ADJACENT, **admitted by explicit operator expansion 2026-09-04** at the p2 review gate. The subprocess-with-timeout pattern reached five copies; the repo rule says refactor once duplication has two concrete consumers.
- `tokenpal/util/proc.py` — new. `async def run_capture(argv: Sequence[str], *, timeout_s: float) -> tuple[int, bytes, bytes]`. Raises `TimeoutError` after killing **and reaping** the child; lets spawn failures propagate as `OSError`.
- `tokenpal/brain/git_nudge.py` — `_git`/`_git_exit_code` call `run_capture`; drop the local proc plumbing and the comment claiming the duplication was deliberate.
- `tokenpal/senses/git/git_sense.py` — `_git`/`_is_dirty` call `run_capture`.
- `tokenpal/actions/git_log.py` — `_run_git` calls `run_capture`, keeping its `(124, b"", b"timeout")` contract.
- `tests/test_util/test_proc.py` — new. Streams and returncode, non-zero exit reported not raised, missing binary raises `OSError`, timeout raises after kill+reap.
- `tokenpal/actions/find_files.py` — new. Shape (proposal):
  ```python
  @register_action
  class FindFilesAction(AbstractAction):
      action_name = "find_files"
      description = (
          "Find files by name (and on macOS by indexed content) under the folders in "
          "[paths] allowed_dirs. Returns paths and modified times, newest first, never "
          "file contents. Use modified_within like '2d' for 'what was I working on'."
      )
      parameters = {  # JSON Schema
          "query": str (required), "kind": enum any|document|image|code|pdf (default any),
          "modified_within": str like "12h" | "2d" | "1w" (optional),
          "limit": int 1..50 (default 20),
      }
      safe = True; requires_confirm = False; cacheable = False  # results go stale
  ```
  `execute` order: validate arguments (empty query → refuse; bad `kind`/`modified_within`/`limit` → refuse naming the argument); `roots = await allowed_roots(load_config().paths.allowed_dirs)` (empty → refuse naming the config key); dispatch on `current_platform()`; post-filter; format.
  Output format: one line per hit, `YYYY-MM-DD HH:MM  <absolute path>`, newest first; `"No matches under <n> allowed folders."` when empty. Never read file bytes.
  **Spotlight backend** (`darwin`): `asyncio.create_subprocess_exec("mdfind", "-0", *["-onlyin", str(r)] for r in roots, predicate)` with a 10 s timeout (`asyncio.wait_for`; on timeout kill the process and return a refusal naming the timeout). Predicate parts, AND'd:
  - `(kMDItemFSName == "*<q>*"cd || kMDItemTextContent == "<q>*"cd)` with `<q>` escaped as locked;
  - kind: `document` → `(kMDItemContentTypeTree == "public.composite-content" || kMDItemContentTypeTree == "public.text")`; `image` → `kMDItemContentTypeTree == "public.image"`; `code` → `kMDItemContentTypeTree == "public.source-code"`; `pdf` → `kMDItemContentTypeTree == "com.adobe.pdf"`; `any` → nothing;
  - `modified_within` → `kMDItemContentModificationDate >= $time.now(-<seconds>)`.
  All four forms were verified live on this Mac (master Background findings). Split stdout on NUL. Missing `mdfind` binary (`FileNotFoundError`) → fall through to the walk.
  **Walk backend** (everything else, and the Spotlight fallback): `await asyncio.to_thread(_walk, roots, query, kind, since_ts, limit)`. `os.walk(root, topdown=True, followlinks=False)`; prune `dirnames` in place using `is_hidden_or_protected`; stop descending past depth 8 relative to the root; stop the whole walk after 50,000 directory entries or 3 s of `time.monotonic()`; match `query.lower() in name.lower()`; `kind` by extension sets: `document` {.pdf .doc .docx .txt .md .rtf .odt .pages .ppt .pptx .xls .xlsx .numbers .csv}, `image` {.png .jpg .jpeg .gif .heic .webp .tiff .svg}, `code` {.py .js .ts .swift .rs .go .c .h .cpp .java .rb .sh .toml .yaml .yml .json}, `pdf` {.pdf}; `since_ts` via `os.stat().st_mtime`. `.key` (Keynote) is rejected by `REJECT_PATH`'s `\.key$` either way; leave it out of the sets (see Decisions). NOTE p1 shipped: `path_is_sensitive` now also rejects the `key/pem/p12/pfx/p8/ovpn/keystore/jks` extension tokens and `id_rsa`/`wallet.dat`/`keeper` substrings, so `.keynote` is safe but any `*.key.*` is refused.
  Collect up to `limit * 4` candidates before the post-filter so filtering does not starve the result, then cap.
- `tokenpal/actions/catalog.py` — `LOCAL_SECTION` (`:54-74`) gains `CatalogEntry("find_files", "Find files under [paths] allowed_dirs by name; newest first, never contents.", kind="local")`.
- `tests/test_actions/test_catalog.py` — add `"find_files"` to the pinned set at `:17-27`.
- `tests/test_actions/test_find_files.py` — new:
  1. predicate builder: query `a"b\c` with `kind="pdf"`, `modified_within="2d"` → the exact argv (assert the escaped predicate string and both `-onlyin` pairs); this is the injection regression test.
  2. `modified_within` parser: `"12h"`, `"2d"`, `"1w"` → seconds; `"2x"`, `"-1d"`, `""` → refusal naming the argument.
  3. `limit` clamp: 0 → refusal, 500 → 50.
  4. temp-tree walk (patch `tokenpal.actions.find_files.current_platform` to return `"linux"`, `tokenpal.actions.find_files.load_config` to return a config whose `paths.allowed_dirs` is `[str(tmp_path / "root")]`, and `tokenpal.util.paths.git_root` to return `None` — otherwise the real repo root joins the roots and `docs/` matches): tree with `root/a.pdf`, `root/sub/b.txt`, `root/.hidden/c.pdf`, `root/Library/d.pdf`, `root/x.env`, `root/credentials.json`, `root/1password-export.csv`, a symlink `root/link.pdf → tmp_path/outside.pdf`. Name every file so it contains `doc` (e.g. `doc-a.pdf`, `sub/doc-b.txt`, `.hidden/doc-c.pdf`, `Library/doc-d.pdf`, `doc.env`, `doc-credentials.json`, `1password-doc.csv`, `doc-link.pdf`), query `"doc"`, and assert exactly `doc-a.pdf` and `sub/doc-b.txt` appear, nothing else does, and the order is newest first (set mtimes with `os.utime`).
  5. Spotlight path with `asyncio.create_subprocess_exec` patched to return NUL-separated paths including one outside the roots and one hidden → both filtered out.
  6. walk bounds: a tree deeper than 8 levels → the deep file is absent; a `followlinks` check with a directory symlink loop does not hang (bounded by the entry cap).
  7. registration: `find_files` is absent from `DEFAULT_TOOLS` and present in `LOCAL_SECTION` (the privacy-contract test already asserts catalog parity, `tests/test_desktop/test_privacy_contract.py:314-319`; this case just names the opt-in intent).

## Decisions & findings

### Shipped at 2026-09-04. Worker + review findings

**Worker deviations (all reported, all accepted):** `_run_backend`'s 6th parameter is the result `limit`, not a candidate cap; the plan's `plat: str` Locked decision was kept over the conflicting "only place `current_platform()` is consulted" Done criterion, so platform is read once in `execute` and branched on only in `_run_backend`; single-clause Spotlight kind predicates are parenthesised uniformly (verified to parse).

**Defects found by review and fixed here, each confirmed by probe:**
- **The two backends disagreed on what a kind IS.** `public.text` is the UTI ancestor of `public.source-code`, so `kind="document"` returned `.py`/`.json` on macOS while `_KIND_EXTS` filed those under `code` — a per-platform behavioural split with no observer, since each test fakes one backend. `_KIND_EXTS` is now the authority, applied in `_post_filter` for both backends; `_KIND_TREES` is demoted to an index-side prefilter and says so.
- **"Newest first" was false on a broad query.** Both backends emit in index/walk order, so sorting after truncation ranks an arbitrary slice; `query="report"` returns 3,914 Spotlight hits against a real `~/Downloads`. `_walk` now keeps a bounded min-heap (`limit * _WALK_RANK_SLACK`, slack because `_post_filter` drops after ranking) and ranks globally; the cap moved into `_spotlight` as `_SPOTLIGHT_CANDIDATE_CAP`, documented as mdfind's limitation rather than a shared property.
- **The kind gate read the wrong path.** It tested the unresolved candidate's suffix while the tool emits the resolved target, so a `report.pdf` symlink to a `.md` answered a `kind="pdf"` search with the `.md` path. Now gated on `resolved.suffix`.
- **An mdfind failure read as "no matches".** `proc.returncode` was never checked and only `FileNotFoundError` fell back to the walk. A non-zero exit now raises and drops to the walk. Note `TimeoutError` subclasses `OSError`, so it is re-raised explicitly ahead of the fallback — broadening the catch without that silently swallowed timeouts (the existing test caught it).
- **A huge first root hid every later root.** `entries` and the deadline were global across the `for root in roots` loop, so exhausting the budget in `~/Documents` meant `~/Desktop` and `~/Downloads` were never opened. The entry budget is now per root; the wall-clock deadline stays global.
- **Wildcards meant different things per backend.** mdfind globs `*`/`?`; the walk compares them literally. Both now search the wildcard-stripped literal.
- **`limit: null` was the only optional argument that refused.** `kind` and `modified_within` tolerated an explicit JSON null; a local model emitting `{"limit": null}` got a refusal. Fixed.

**Operator decisions at the p2 review gate (2026-09-04):**
1. **Content oracle accepted.** The Spotlight predicate's `kMDItemTextContent` clause means a query can match a file by its *contents* and return the path — probed: a file named `notes.txt` containing `zqxwvunique` is returned by `query="zqxwvunique"`. The tool keeps no `reads_desktop_content` marker and its results persist. Recorded in `CLAUDE.md`'s Privacy section so the contract stops reading as if the marked tier is the only content path.
2. **Empty `allowed_dirs` disables the tools** (see the p1 shard).
3. **The subprocess-with-timeout duplication was extracted across all five sites**, not filed: `tokenpal/util/proc.py::run_capture`, now used by `git_log`, `grep_codebase`, `git_nudge`, `git_sense` and `find_files`. It fixed a latent bug on the way — `git_log._run_git` killed on timeout without reaping, leaking a zombie.

**Unresolved flake, observed once:** `tests/test_brain/test_tool_loop.py::test_reenabling_tool_calling_keeps_desktop_tools_out_of_conversation` failed once during a full-suite run at this phase's tip (`assert brain._tool_calling_enabled is True` → False), then passed in isolation and in **15 consecutive full-suite runs** afterwards. It also passes at HEAD in a clean worktree, and the suite has no order randomiser, so the same order both failed and passed. Not reproduced, cause unknown, not demonstrably caused by this diff — recorded so the next person who sees it knows it is the second sighting, not the first.

**Known and accepted, not defects of this phase:** a case-mismatched `allowed_dirs` entry (`~/documents`) makes the Spotlight backend return nothing on macOS while the walk works, because mdfind emits canonically-cased paths and POSIX containment is strict — this is p1's locked case policy and `config.default.toml` warns about it. `GitSense` has no test coverage in the suite (`grep -rn GitSense tests/` is empty), so its migration was verified by a live probe instead: `_git` returns branch and subject, a bogus subcommand returns `""`, `_is_dirty` is True on a dirty tree, `poll()` reads "On branch main, uncommitted changes".
### Decision: one escaped predicate over `-name`  *(status: active)*
- **Rationale:** `-name` is injection-safe (argv) but cannot express `name OR content`; the escaped predicate was probed against a literal `"` in the query and matched only the target.
- **Alternatives considered:** two `mdfind` invocations merged in Python — double the subprocess cost for no gain.
- **Evidence:** master Background findings (Spotlight injection probe; `mdfind` facts).

### Decision: `.key` files are unreachable by design  *(status: active)*
- **Rationale:** `REJECT_PATH` matches `\.key$` (`tokenpal/util/paths.py` after p1, originally `read_file.py:15`), so Keynote decks never appear. Accepted: the regex predates this tool and protects key files; loosening it is a separate decision.

### Decision: `cacheable = False`  *(status: active)*
- **Rationale:** the agent's in-run cache (`tokenpal/brain/agent.py:317`) would replay a stale listing after the user saves a file mid-run.

## Failure modes to anticipate
- `mdfind` prints `[UserQueryParser]` noise to stderr with `-name`; with a predicate it is quiet, but capture stderr to `DEVNULL` anyway.
- A query of only wildcard characters (`*`) would match everything under the roots; refuse queries shorter than 2 non-wildcard characters.
- `kMDItemTextContent == "<q>*"cd` is prefix-of-word matching; do not promise substring content matches in the description.
- `os.walk` over a root that is itself a symlink: roots are resolved by `allowed_roots`, fine; but a `dirnames` entry that is a symlink to a parent would loop without `followlinks=False`. Keep it False.
- The tool description reaches the LLM on every turn once enabled; keep it under ~60 tokens.
- `current_platform` is `lru_cache`d (`tokenpal/util/platform.py:9-14`): tests patch `tokenpal.actions.find_files.current_platform`, not `platform.system`.

## Done criteria
- `grep_codebase` emits exactly one per-file-cap flag, and a 100-line result carries no "capped" suffix; both pinned by tests in `tests/test_actions/test_grep_codebase.py`.
- The seven tests above run and pass; `pytest` green; `ruff` and `mypy` clean.
- Live on this Mac after enabling `find_files` in the `/tools` picker: in `/agent`, "what pdfs did I download in the last week" produces a `find_files` call whose result lists paths under `~/Downloads` with dates, newest first, and no path under a hidden directory or `~/Library`.
- `grep -n "open(" tokenpal/actions/find_files.py` shows no file read (the tool never opens a result).
- `_run_backend` is the only place `current_platform()` is consulted in the module (p4 adds its branch there).
