"""Read a git-tracked file's contents (capped, sensitive-path rejected)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.util.paths import REJECT_PATH, git_root, path_is_sensitive, resolve_inside

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

    async def execute(self, **kwargs: Any) -> ActionResult:
        path_arg = kwargs.get("path", "")
        if not isinstance(path_arg, str) or not path_arg.strip():
            return ActionResult(output="Path is required.", success=False)

        if REJECT_PATH.search(path_arg):
            return ActionResult(output="Path matches a denied pattern.", success=False)

        if contains_sensitive_term(path_arg):
            return ActionResult(output="Path references a sensitive app.", success=False)

        root = await git_root(Path.cwd())
        if root is None:
            return ActionResult(output="Not inside a git repository.", success=False)

        inside = resolve_inside(root / path_arg, [root.resolve()])
        if inside is None:
            return ActionResult(output="Path is outside the git repo.", success=False)
        abs_path, _, rel = inside
        rel = Path(rel).as_posix()

        # A symlink resolves to a name the caller never spelled, so the denylist
        # has to run again on what is actually opened.
        if path_is_sensitive(rel):
            return ActionResult(output="Path matches a denied pattern.", success=False)

        # Both spellings must be tracked. The spelled name alone lets an untracked
        # symlink read a tracked file; the resolved target alone lets a tracked
        # symlink stand in for one the denylist refuses under its own name.
        spelled = _spelled_rel(path_arg, root)
        if spelled is None or not await _git_ls_files_contains(root, spelled):
            return ActionResult(output="File is not tracked by git.", success=False)
        if spelled != rel and not await _git_ls_files_contains(root, rel):
            return ActionResult(output="File is not tracked by git.", success=False)

        try:
            with open(abs_path, "rb") as fh:
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
