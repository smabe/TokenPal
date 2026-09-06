"""Ripgrep wrapper — search the current repo, capped, .gitignore-respecting."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.util.paths import ResolvedPath, git_root
from tokenpal.util.proc import run_capture

# Total lines returned to the caller, enforced in Python: ripgrep has no
# whole-run match cap, only the per-file --max-count below.
_MAX_MATCHES = 100
_MAX_PER_FILE = 5
_TIMEOUT_S = 10.0


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
        path_arg = kwargs.get("path")
        if isinstance(path_arg, ResolvedPath):
            # The resolved value, not the argument: rg walks whatever argv says.
            target = path_arg.resolved
        elif path_arg is None or (isinstance(path_arg, str) and not path_arg.strip()):
            # `path` is optional, so the invoker resolved nothing and the repo
            # root is the target. Refused outside one: "the current repo" has no
            # meaning there, and the cwd is not a bounded search.
            root = await git_root(Path.cwd())
            if root is None:
                return ActionResult(output="Not inside a git repository.", success=False)
            target = root
        else:
            # Never fall through to the whole repo on an argument shape the
            # invoker declined to contain.
            return ActionResult(output="Path is required.", success=False)

        cmd = [
            rg,
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
        lines = text.splitlines()
        kept = [ln for ln in lines if not contains_sensitive_term(ln)]
        if len(kept) > _MAX_MATCHES:
            kept = kept[:_MAX_MATCHES]
            kept.append(f"... [capped at {_MAX_MATCHES} matches]")

        if not kept:
            return ActionResult(output="No matches.")
        return ActionResult(output="\n".join(kept))
