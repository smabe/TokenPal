"""git_log, git_diff, git_status — thin read-only wrappers around git."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term

_LOG_LIMIT_CAP = 50
_DIFF_MAX_BYTES = 50 * 1024
_TIMEOUT_S = 10.0


async def _run_git(args: list[str]) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(Path.cwd()),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        return 124, b"", b"timeout"
    return proc.returncode or 0, stdout, stderr


def _filter_sensitive(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not contains_sensitive_term(line)
    )


@register_action
class GitLogAction(AbstractAction):
    action_name = "git_log"
    description = "Show recent git commits in the current repo (oneline, dated)."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": "Git-parseable date or phrase, e.g. 'yesterday', '2 weeks ago'.",
            },
            "author": {
                "type": "string",
                "description": "Filter by author name or email substring.",
            },
            "limit": {
                "type": "integer",
                "description": f"Max commits to return (capped at {_LOG_LIMIT_CAP}).",
            },
        },
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        limit = kwargs.get("limit", 20)
        if not isinstance(limit, int) or limit < 1:
            limit = 20
        limit = min(limit, _LOG_LIMIT_CAP)

        args = ["log", "--oneline", "--date=short", f"-n{limit}"]

        since = kwargs.get("since")
        if isinstance(since, str) and since.strip():
            args.append(f"--since={since.strip()}")

        author = kwargs.get("author")
        if isinstance(author, str) and author.strip():
            args.append(f"--author={author.strip()}")

        rc, stdout, stderr = await _run_git(args)
        if rc != 0:
            msg = stderr.decode("utf-8", errors="replace").strip() or "git log failed"
            return ActionResult(output=msg, success=False)

        text = stdout.decode("utf-8", errors="replace")
        filtered = _filter_sensitive(text).strip()
        if not filtered:
            return ActionResult(output="No commits.")
        return ActionResult(output=filtered)


@register_action
class GitDiffAction(AbstractAction):
    action_name = "git_diff"
    description = "Show the current git diff. Defaults to working tree vs HEAD. Capped at 50KB."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Optional ref to diff against (e.g. 'main', 'HEAD~5').",
            },
        },
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        args = ["diff"]
        ref = kwargs.get("ref")
        if isinstance(ref, str) and ref.strip():
            args.append(ref.strip())

        rc, stdout, stderr = await _run_git(args)
        if rc != 0:
            msg = stderr.decode("utf-8", errors="replace").strip() or "git diff failed"
            return ActionResult(output=msg, success=False)

        blob = stdout[:_DIFF_MAX_BYTES]
        text = blob.decode("utf-8", errors="replace")
        if len(stdout) > _DIFF_MAX_BYTES:
            text += f"\n... [truncated at {_DIFF_MAX_BYTES} bytes]"
        if not text.strip():
            return ActionResult(output="No diff.")
        return ActionResult(output=text)


@register_action
class GitStatusAction(AbstractAction):
    action_name = "git_status"
    description = "Show the current git status in porcelain form."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        rc, stdout, stderr = await _run_git(["status", "--porcelain=v1"])
        if rc != 0:
            msg = stderr.decode("utf-8", errors="replace").strip() or "git status failed"
            return ActionResult(output=msg, success=False)

        text = stdout.decode("utf-8", errors="replace")
        filtered = _filter_sensitive(text).strip()
        if not filtered:
            return ActionResult(output="Working tree clean.")
        return ActionResult(output=filtered)
