"""Ripgrep wrapper — search the current repo, capped, .gitignore-respecting."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term

_MAX_MATCHES = 100
_TIMEOUT_S = 10.0


async def _git_root(start: Path) -> Path | None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(start),
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    out = stdout.decode("utf-8", errors="replace").strip()
    return Path(out) if out else None


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

    async def execute(self, **kwargs: Any) -> ActionResult:
        pattern = kwargs.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            return ActionResult(output="Pattern is required.", success=False)

        rg = shutil.which("rg")
        if rg is None:
            return ActionResult(output="ripgrep (rg) is not installed.", success=False)

        root = await _git_root(Path.cwd()) or Path.cwd()

        target: Path
        path_arg = kwargs.get("path")
        if isinstance(path_arg, str) and path_arg.strip():
            if contains_sensitive_term(path_arg):
                return ActionResult(output="Path references a sensitive app.", success=False)
            candidate = Path(path_arg)
            target = candidate if candidate.is_absolute() else (root / candidate)
        else:
            target = root

        cmd = [
            rg,
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count=5",
            "-m",
            str(_MAX_MATCHES),
            "--",
            pattern,
            str(target),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
        except TimeoutError:
            return ActionResult(output="Search timed out.", success=False)
        except OSError as e:
            return ActionResult(output=f"Failed to run ripgrep: {e}", success=False)

        if proc.returncode not in (0, 1):
            return ActionResult(output="ripgrep reported an error.", success=False)

        text = stdout.decode("utf-8", errors="replace")
        lines = text.splitlines()
        kept = [ln for ln in lines if not contains_sensitive_term(ln)]
        if len(kept) >= _MAX_MATCHES:
            kept = kept[:_MAX_MATCHES]
            kept.append(f"... [capped at {_MAX_MATCHES} matches]")

        if not kept:
            return ActionResult(output="No matches.")
        return ActionResult(output="\n".join(kept))
