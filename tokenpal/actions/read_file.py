"""Read a git-tracked file's contents (capped, sensitive-path rejected)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term

_MAX_BYTES = 200 * 1024
_REJECT_PATH = re.compile(r"\.env|credentials|secrets|\.key$|\.pem$", re.IGNORECASE)


async def _git_ls_files_contains(repo_root: Path, rel: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_root),
        "ls-files",
        "--error-unmatch",
        rel,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    return rc == 0


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
class ReadFileAction(AbstractAction):
    action_name = "read_file"
    description = "Read the contents of a git-tracked file in the current repo. Capped at 200KB."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file. Relative paths resolve against the git root.",
            },
        },
        "required": ["path"],
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        path_arg = kwargs.get("path", "")
        if not isinstance(path_arg, str) or not path_arg.strip():
            return ActionResult(output="Path is required.", success=False)

        if _REJECT_PATH.search(path_arg):
            return ActionResult(output="Path matches a denied pattern.", success=False)

        if contains_sensitive_term(path_arg):
            return ActionResult(output="Path references a sensitive app.", success=False)

        root = await _git_root(Path.cwd())
        if root is None:
            return ActionResult(output="Not inside a git repository.", success=False)

        candidate = Path(path_arg)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(root.resolve())
            except ValueError:
                return ActionResult(output="Path is outside the git repo.", success=False)
        else:
            rel = candidate

        if not await _git_ls_files_contains(root, str(rel)):
            return ActionResult(output="File is not tracked by git.", success=False)

        abs_path = root / rel
        try:
            with open(abs_path, "rb") as fh:
                blob = fh.read(_MAX_BYTES + 1)
        except OSError as e:
            return ActionResult(output=f"Failed to read file: {e}", success=False)

        truncated = len(blob) > _MAX_BYTES
        text = blob[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += f"\n... [truncated at {_MAX_BYTES} bytes]"
        return ActionResult(output=text)
