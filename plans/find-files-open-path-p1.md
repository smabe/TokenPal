# find-files-open-path-p1 — shared path safety + `[paths] allowed_dirs`

You are phase `p1` of the `find-files-open-path` plan. This phase delivers, as one commit, a shared path-safety module the two new tools (p2, p3) and later `read_document` (#54) build on, the `[paths] allowed_dirs` config key, and the refactor of `read_file`/`grep_codebase` onto the shared helpers. No new tool ships here.

## Locked decisions
See the master `plans/find-files-open-path.md`. The decisions binding this phase:
- Containment is `Path(...).expanduser().resolve()` then `is_relative_to` against each resolved root. On Windows both sides pass through `os.path.normcase` first (it folds case there); on POSIX the comparison is strict, so an `allowed_dirs` entry whose case differs from the on-disk folder refuses everything under it rather than admitting anything (`os.path.normcase` is the identity on POSIX; master Background findings). Never a lexical prefix check.
- `resolve_inside` returns `(resolved, root, rel)`. **Amended at p1 review (operator, 2026-09-04)** from the approved pair: two review angles found that p2 and p3 both hand-derived `relative_to(root)` beside a helper deriving it again, and that `is_hidden_or_protected`'s `relative_to` precondition could not be carried by its signature. p2/p3 shard text swept to match.
- `allowed_dirs` default is `["~/Documents", "~/Desktop", "~/Downloads"]` as strings, expanded at use sites like `data_dir` (`tokenpal/app.py:144`); the git root of `Path.cwd()` is always appended when one exists.
- The path sensitivity filter is `contains_sensitive_content_term` plus `_SENSITIVE_PATH_TERMS` (`keychain`, `keeper`, `id_rsa`, `id_ed25519`, `id_ecdsa`, `id_dsa`, `wallet.dat`) plus `_SENSITIVE_EXTS` (`key pem p12 pfx p8 ovpn keystore jks`) matched per dot-separated token, applied to the path *relative to its root*. **Widened at p1 review (operator, 2026-09-04)** from the approved narrow-list-plus-keychain: probes showed `backup/id_rsa`, `certs/client.p12`, `apple/AuthKey.p8`, `wallet.dat` and `keeper-vault-export.csv` all passing as benign under `~/Documents`, a default allowed root. `signal-*`/`health-*` stay allowed as the master's Done criteria require. `REJECT_PATH` was deliberately NOT widened — `read_file` consumes it and must stay behavior-identical. `read_file` keeps its own broad check unchanged (master Non-goals).
- `_git_root` exists twice verbatim (`tokenpal/actions/read_file.py:33-47`, `tokenpal/actions/grep_codebase.py:18-32`); this phase leaves exactly one copy, in `tokenpal/util/paths.py`, and both consumers import it.

## Work
- Scope trace: PREREQUISITE — p2 and p3 cannot validate a path without `resolve_inside`/`allowed_roots`, and cannot filter without `path_is_sensitive`/`is_hidden_or_protected`; the `read_file`/`grep_codebase` refactor is the repo's reuse-first rule applied to a helper gaining its third consumer.
- `tokenpal/util/paths.py` — new module. Shape (proposal; names are proposals, shapes are contract):
  ```python
  REJECT_PATH = re.compile(r"\.env|credentials|secrets|\.key$|\.pem$", re.IGNORECASE)
  # moved verbatim from tokenpal/actions/read_file.py:15

  async def git_root(start: Path) -> Path | None
  # moved verbatim from read_file.py:33-47 (== grep_codebase.py:18-32)

  async def allowed_roots(configured: Sequence[str]) -> list[Path]
  # expanduser().resolve() each entry that is_dir(); append await git_root(Path.cwd())
  # when not None and not already present. Order preserved, no duplicates.

  def resolve_inside(candidate, roots) -> tuple[Path, Path, str] | None   # AS SHIPPED
  # expanduser().resolve(strict=False); for each root, compare
  # Path(os.path.normcase(str(resolved))).is_relative_to(Path(os.path.normcase(str(root))))
  # (normcase folds case on Windows only); return (resolved, root, rel) for the first
  # match, else None. Empty roots -> None. Does NOT check existence (callers decide).
  # Unresolvable input (~nosuchuser, embedded NUL) returns None, not an exception.

  def is_hidden_or_protected(path: Path, root: Path) -> bool              # AS SHIPPED
  # Case-insensitive on the component names. True when path is under
  # Path.home()/"Library" (compared part-wise, so ~/Library2 is not matched), when any
  # component of path.relative_to(root) starts with "." or lowercases to "library" or
  # "node_modules", or when path is not under root at all (refuse, never raise).

  def path_is_sensitive(rel: str) -> bool                                  # AS SHIPPED
  # REJECT_PATH.search(rel) or contains_sensitive_content_term(rel)
  # or any _SENSITIVE_PATH_TERMS substring or any dot-separated token in _SENSITIVE_EXTS
  ```
  `contains_sensitive_content_term` is at `tokenpal/brain/personality.py:293`; `tokenpal/util/untrusted_text.py:13-14` already imports it from there, so the same import creates no cycle (`personality.py` imports only `tokenpal.tools.voice_profile`, `tokenpal.util.text_guards`, `tokenpal.util.timefmt`).
- `tokenpal/actions/read_file.py` — delete `_REJECT_PATH` (`:15`) and `_git_root` (`:33-47`); `from tokenpal.util.paths import REJECT_PATH, git_root`; update the two call sites (`:72`, `:78`). Behavior byte-identical.
- `tokenpal/actions/grep_codebase.py` — delete `_git_root` (`:18-32`); import `git_root`; update its call site. Behavior byte-identical.
- `tokenpal/config/schema.py` — `PathsConfig` (`:228-229`) gains `allowed_dirs: list[str] = field(default_factory=lambda: ["~/Documents", "~/Desktop", "~/Downloads"])`. The loader assigns TOML arrays verbatim (`tokenpal/config/loader.py:100-115`); precedent `FilesystemPulseConfig.roots` (`schema.py:58-61`).
- `config.default.toml` — under `[paths]` (`:276-277`) add `allowed_dirs = ["~/Documents", "~/Desktop", "~/Downloads"]` with a three-line comment: roots the file tools (`find_files`, `open_path`) may search and open; the current git repo is always included; on macOS/Linux an entry must match the folder's on-disk case.
- `tests/test_util/__init__.py` — new, empty (`tests/test_util/` does not exist at f27db56).
- `tests/test_util/test_paths.py` — new. Cases, each on a `tmp_path` tree:
  1. symlink inside root → file outside root: `resolve_inside` returns `None`.
  2. `root/../outside`: `None`.
  3. case policy: on POSIX (`os.name != "nt"`), a root passed as `str(root).upper()` refuses a real candidate under `root` (strict); on Windows the same call accepts it (`normcase`). Write both branches, skip the one that does not apply to the running host.
  4. nonexistent candidate inside root: returns `(resolved, root, rel)` (existence is the caller's job).
  5. `allowed_roots` drops entries that are not directories, expands `~`, appends the git root when `git_root` is monkeypatched to return a path, and never duplicates it.
  6. `is_hidden_or_protected`: `.ssh/id_rsa`, `Library/x`, `node_modules/x` → True; `Documents/a.pdf` → False.
  7. `path_is_sensitive`: `x.env`, `credentials.json`, `keychain-backup.txt`, `1password-export.csv` → True; `health-tracker.csv`, `signal-report.md` → False. **As shipped, also:** `backup/id_rsa`, `certs/client.p12`, `apple/AuthKey.p8`, `vpn/work.ovpn`, `server.key.bak`, `wallet.dat`, `keeper-vault-export.csv` → True; `notes.keynote`, `hockey.pdf` → False.
  8. `tests/test_actions/test_read_file.py:42-45` and the grep_codebase suite stay green unchanged.

## Decisions & findings

### Shipped at 2026-09-04. Worker + review findings

**Worker (empirical):**
- `Path("").expanduser().resolve()` is `cwd` and `is_dir()` is True, so a blank `allowed_dirs` entry would silently admit the whole working directory. `allowed_roots` skips blank entries.
- `is_hidden_or_protected` must check `~/Library` BEFORE `relative_to(root)`: a root configured at `~/Library/Mail` strips the `Library` component and the parts loop cannot see it.
- `SENSITIVE_CONTENT_TERMS` (`personality.py:272-278`) contains `1password` and does not contain `health`, `signal`, `keychain`, `chase`, `fidelity` — the master's fixture assumption held exactly.
- p2/p3 must patch `tokenpal.util.paths.git_root`, not the importing action's namespace: `allowed_roots` resolves it through the module global at call time.

**Review defects fixed in this phase's own code (all confirmed by probe before and after):**
- `allowed_dirs` as a bare TOML string iterated character by character and admitted `/` as a root — `resolve_inside("/etc/passwd", roots)` matched. The loader does zero type coercion and `mypy` gave false confidence from the `list[str]` annotation. `allowed_roots` now wraps a scalar as one entry.
- `except OSError` was too narrow: `~nosuchuser/x` raises `RuntimeError` and an embedded NUL raises `ValueError`, both escaping the helper. One stale `~olduser/...` entry would have taken down every file tool. Widened to `(OSError, ValueError, RuntimeError)` in both places.
- The `Library`/`node_modules` guard was case-sensitive, so `~/library/Mail/x` passed on APFS. Now compared lowercased; the home-Library check compares path *parts*, not a string prefix, so `~/Library2` is not false-matched.
- `is_hidden_or_protected` raised `ValueError` on a non-descendant instead of answering. Now returns True (refuse).

**Reported, not fixed (out of this phase's scope):**
- `read_file`'s relative-path branch skips containment entirely; a tracked symlink `notes.txt -> ../secret/id_rsa` is read while the same file by absolute path is refused. Pre-existing, `read_file` policy is a plan Non-goal. Operator chose to file: **#66**.
- `grep_codebase.py:68-71` passes both `--max-count=5` and `-m 100`; the later wins, so the per-file cap is 100, not 5 (demonstrated with real `rg` 15.2.0). One noisy file starves the whole result set. Secondary off-by-one at `:94`: `>= _MAX_MATCHES` reports "capped" when exactly 100 survived and nothing was dropped. Pre-existing, ADJACENT, not filed — awaiting operator disposition.
- `REJECT_PATH` is unanchored for `.env`/`credentials`/`secrets` but end-anchored for `.key`/`.pem`. Under `~/Documents` this false-positives on ordinary documents (`Trade Secrets (novel).pdf`, `My Credentials CV.pdf`) and blanks their subtree from `find_files` results with no explanation. A p2 usability question, not a p1 defect.

**Rejected with evidence:** unifying `_PROTECTED_COMPONENTS` with `filesystem_pulse`'s `_EXCLUDED_DIR_NAMES` (couples a watchdog noise knob to a security boundary); micro-optimising `resolve_inside`/`is_hidden_or_protected` (12.8 µs/call is ~24 ms across a realistic 1,837-file `~/Downloads` walk, behind an `mdfind` call and an LLM round trip).
### Decision: config read at execute time, not injected  *(status: active)*
- **Rationale:** `resolve_actions` is called without `action_configs` (`tokenpal/app.py:228-231`), so `self._config` is `{}` at runtime for every action. `sunrise_sunset.py:17-23` already reads `load_config()` lazily. Live edits to `allowed_dirs` take effect without restart.
- **Alternatives considered:** wiring `action_configs` in `app.py` — rejected as a second mechanism for the same value while the first has no consumer today (parking lot).
- **Evidence:** cited inline.

### Decision: `allowed_roots` takes the configured list, not the config object  *(status: active)*
- **Rationale:** keeps `tokenpal.util` free of a `tokenpal.config.loader` import; p2/p3 call `await allowed_roots(load_config().paths.allowed_dirs)`.
- **Evidence:** `tokenpal/util/` modules import nothing from `tokenpal.config` today (`grep -rn "tokenpal.config" tokenpal/util/` → none at f27db56).

## Failure modes to anticipate
- `Path.resolve(strict=False)` on macOS keeps the typed case, and `os.path.normcase` does nothing on POSIX, so a root configured as `~/documents` refuses every real path under `~/Documents`. That is the chosen, safe direction; test 3 pins it and the config comment warns.
- `is_relative_to` on `str` inputs: convert `normcase` output back to `Path` before calling it; do not compare raw strings with `startswith` (`~/Documents2` would pass for `~/Documents`).
- `git_root` shells out; `allowed_roots` is therefore async. Do not make it sync by using `Path.cwd()` heuristics.
- The ruff `N` rules: `REJECT_PATH` is a module constant, fine; keep function names snake_case.

## Done criteria
- `tests/test_util/test_paths.py` runs and passes with the eight cases above; the symlink case demonstrably fails on a lexical check (write it once with `str(p).startswith(str(root))` in a comment-free assertion of the resolved result, not as a second implementation).
- `grep -rn "_git_root\|_REJECT_PATH" tokenpal/` returns nothing.
- `pytest tests/test_actions/test_read_file.py tests/test_actions/test_grep_codebase.py tests/test_util/` green; full `pytest` green; `ruff check tokenpal/` and `mypy tokenpal/ --ignore-missing-imports` clean.
- `tokenpal --check` still lists `read_file` and `grep_codebase` as loaded actions (observable that the refactor did not break discovery; `discover_actions` swallows ImportError, `tokenpal/actions/registry.py:27-33`).
