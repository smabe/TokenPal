"""Read a git-tracked file's contents (capped, sensitive-path rejected)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.util.paths import ResolvedPath

_MAX_BYTES = 200 * 1024


def _spelled_rel(path_arg: str, root: Path) -> str | None:
    """Root-relative posix spelling of ``path_arg`` with no symlink resolution."""
    spelled = Path(path_arg)
    try:
        return (spelled if spelled.is_absolute() else root / spelled).relative_to(root).as_posix()
    except ValueError:
        return None


async def _git_ls_files_contains(repo_root: Path, rel: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_root),
        "ls-files",
        "--error-unmatch",
        "--",
        rel,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    return rc == 0


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
    path_params = ("path",)
    path_roots = "git_root"
    path_screen = "broad"

    async def execute(self, **kwargs: Any) -> ActionResult:
        path = kwargs.get("path")
        if not isinstance(path, ResolvedPath):
            return ActionResult(output="Path is required.", success=False)

        # Both spellings must be tracked. The spelled name alone lets an untracked
        # symlink read a tracked file; the resolved target alone lets a tracked
        # symlink stand in for one the denylist refuses under its own name.
        spelled = _spelled_rel(path.raw, path.root)
        if spelled is None or not await _git_ls_files_contains(path.root, spelled):
            return ActionResult(output="File is not tracked by git.", success=False)
        if spelled != path.rel and not await _git_ls_files_contains(path.root, path.rel):
            return ActionResult(output="File is not tracked by git.", success=False)

        try:
            with open(path.resolved, "rb") as fh:
                blob = fh.read(_MAX_BYTES + 1)
        except OSError as e:
            return ActionResult(
                output=f"Failed to read file: {e.strerror or type(e).__name__}.", success=False
            )

        truncated = len(blob) > _MAX_BYTES
        text = blob[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += f"\n... [truncated at {_MAX_BYTES} bytes]"
        return ActionResult(output=text)
