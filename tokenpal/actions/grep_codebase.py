"""Ripgrep wrapper — search the current repo, capped, .gitignore-respecting."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.util.paths import (
    PathScreen,
    ResolvedPath,
    git_root,
    is_screened_out,
    resolve_inside,
)
from tokenpal.util.proc import run_capture

# Total lines returned to the caller, enforced in Python: ripgrep has no
# whole-run match cap, only the per-file --max-count below.
_MAX_MATCHES = 100
_MAX_PER_FILE = 5
_TIMEOUT_S = 10.0


async def _ignored_paths(paths: list[str], root: Path) -> set[str]:
    """The subset of *paths* git ignores, per the repo's own rules.

    ripgrep honours ``.gitignore`` while it walks, but not for a path named on
    its command line, so a search pointed straight at an ignored folder reads
    it. Asking git is the only answer that matches the user's own ignore rules.

    ``-z --stdin`` rather than argv: git C-quotes any output path holding a
    non-ASCII byte, a quote, a backslash, a tab or a control character, and a
    quoted path would never match rg's raw string -- so the screen would fail
    OPEN on exactly the paths hardest to eyeball. NUL-separated output is
    verbatim, and stdin removes the ARG_MAX ceiling on a broad match.
    """
    if not paths:
        return set()
    try:
        rc, stdout, _ = await run_capture(
            ["git", "-C", str(root), "check-ignore", "-z", "--stdin"],
            timeout_s=_TIMEOUT_S,
            stdin_data="\0".join(paths).encode("utf-8"),
        )
    except (OSError, TimeoutError):
        # Fail closed: an unanswerable ignore question withholds every hit
        # rather than guessing that nothing is ignored.
        return set(paths)
    if rc not in (0, 1):
        return set(paths)
    return set(stdout.decode("utf-8", errors="replace").split("\0")) - {""}


def _attributed_hits(lines: list[str], target: Path) -> list[tuple[str, str]]:
    """``(path to screen, line to return)`` per rg record, dropping the rest.

    ``--null`` separates the path from the match because a path may contain a
    colon, and rg omits the path entirely when the target is a single file.
    A record that carries no separator, or whose path is not under the target,
    is a split artefact — a filename containing a newline produces one — and is
    dropped rather than resolved, since resolving it would anchor at the cwd.
    """
    prefix = str(target)
    single_file = target.is_file()
    out: list[tuple[str, str]] = []
    for line in lines:
        raw, sep, rest = line.partition("\0")
        if not sep:
            # rg omits the path when the target is a single file, and that hit
            # belongs to the target. Its pathless shape is preserved.
            if single_file and line:
                out.append((prefix, line))
            continue
        if not single_file and not raw.startswith(prefix):
            continue
        out.append((raw, f"{raw}:{rest}"))
    return out


async def _screened_hits(
    lines: list[str], target: Path, root: Path, screen: PathScreen
) -> list[str]:
    """The rg hits whose file survives the output screen, still ``path:line:text``."""
    attributed = _attributed_hits(lines, target)
    ignored = await _ignored_paths(sorted({raw for raw, _ in attributed}), root)
    verdict: dict[str, bool] = {}
    kept: list[str] = []
    for raw, out_line in attributed:
        allowed = verdict.get(raw)
        if allowed is None:
            match = resolve_inside(raw, [root])
            allowed = (
                raw not in ignored
                and match is not None
                and not is_screened_out(match[0], match[1], Path(match[2]).as_posix(), screen)
            )
            verdict[raw] = allowed
        if allowed:
            kept.append(out_line)
    return kept


@register_action
class GrepCodebaseAction(AbstractAction):
    action_name = "grep_codebase"
    description = (
        "Search the current repo with ripgrep. Respects .gitignore. "
        "Capped at 100 matches."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex or literal pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "Optional subdirectory to restrict the search.",
            },
        },
        "required": ["pattern"],
    }
    safe = True
    requires_confirm = False
    path_params = ("path",)
    path_roots = "git_root"
    path_screen = "broad"

    async def execute(self, **kwargs: Any) -> ActionResult:
        pattern = kwargs.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            return ActionResult(output="Pattern is required.", success=False)

        rg = shutil.which("rg")
        if rg is None:
            return ActionResult(output="ripgrep (rg) is not installed.", success=False)

        target: Path
        root: Path
        path_arg = kwargs.get("path")
        if isinstance(path_arg, ResolvedPath):
            # The resolved value, not the argument: rg walks whatever argv says.
            target = path_arg.resolved
            root = path_arg.root
        elif path_arg is None or (isinstance(path_arg, str) and not path_arg.strip()):
            # `path` is optional, so the invoker resolved nothing and the repo
            # root is the target. Refused outside one: "the current repo" has no
            # meaning there, and the cwd is not a bounded search.
            repo = await git_root(Path.cwd())
            if repo is None:
                return ActionResult(output="Not inside a git repository.", success=False)
            root = repo
            target = repo
        else:
            # Never fall through to the whole repo on an argument shape the
            # invoker declined to contain.
            return ActionResult(output="Path is required.", success=False)

        cmd = [
            rg,
            # The path is NUL-separated from the match: a path may contain a colon.
            "--null",
            "--line-number",
            "--no-heading",
            "--color=never",
            f"--max-count={_MAX_PER_FILE}",
            "--",
            pattern,
            str(target),
        ]
        try:
            returncode, stdout, _ = await run_capture(cmd, timeout_s=_TIMEOUT_S)
        except TimeoutError:
            return ActionResult(output="Search timed out.", success=False)
        except OSError as e:
            return ActionResult(output=f"Failed to run ripgrep: {e}", success=False)

        if returncode not in (0, 1):
            return ActionResult(output="ripgrep reported an error.", success=False)

        text = stdout.decode("utf-8", errors="replace")
        lines = await _screened_hits(text.split("\n"), target, root, self.path_screen)
        kept = [ln for ln in lines if not contains_sensitive_term(ln)]
        if len(kept) > _MAX_MATCHES:
            kept = kept[:_MAX_MATCHES]
            kept.append(f"... [capped at {_MAX_MATCHES} matches]")

        if not kept:
            return ActionResult(output="No matches.")
        return ActionResult(output="\n".join(kept))
