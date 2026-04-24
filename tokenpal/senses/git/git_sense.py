"""Git sense — watches for new commits and branch changes."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)


@register_sense
class GitSense(AbstractSense):
    sense_name = "git"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 15.0
    reading_ttl_s = 300.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._last_head: str = ""
        self._last_branch: str = ""
        self._last_dirty: bool = False
        self._pending_reading: SenseReading | None = None
        self._pending_expires: float = 0.0

    async def setup(self) -> None:
        if not shutil.which("git"):
            log.warning("git not found — disabling git sense")
            self.disable()
            return
        # Snapshot initial state so the first poll doesn't fire a false change
        self._last_head, self._last_branch, self._last_dirty = await asyncio.gather(
            self._git("rev-parse", "--short", "HEAD"),
            self._git("rev-parse", "--abbrev-ref", "HEAD"),
            self._is_dirty(),
        )

    async def poll(self) -> SenseReading | None:
        head, branch, dirty = await asyncio.gather(
            self._git("rev-parse", "--short", "HEAD"),
            self._git("rev-parse", "--abbrev-ref", "HEAD"),
            self._is_dirty(),
        )

        if not head:
            return None

        new_commits = ""
        changed_from = ""

        if head != self._last_head and self._last_head:
            new_commits = await self._git(
                "log", f"{self._last_head}..{head}", "--oneline", "--no-color",
            )
            changed_from = f"HEAD was {self._last_head}"
        if branch != self._last_branch and self._last_branch:
            branch_change = f"switched from {self._last_branch} to {branch}"
            changed_from = f"{changed_from}, {branch_change}" if changed_from else branch_change
        if dirty != self._last_dirty:
            dirty_change = "now dirty" if dirty else "now clean"
            changed_from = f"{changed_from}, {dirty_change}" if changed_from else dirty_change

        self._last_head = head
        self._last_branch = branch
        self._last_dirty = dirty

        # Pull the HEAD commit timestamp + message on every poll so the
        # proactive-git detector can compute staleness without an extra
        # subprocess round-trip. Cheap: one `git log -1` call.
        last_commit_ts_raw, last_commit_msg = await asyncio.gather(
            self._git("log", "-1", "--format=%ct"),
            self._git("log", "-1", "--format=%s"),
        )
        try:
            last_commit_ts: float | None = float(last_commit_ts_raw)
        except ValueError:
            last_commit_ts = None

        if not changed_from:
            # Keep returning the reading long enough for the gate to open (~2 min)
            # but clear changed_from so the urgent bypass only fires once
            if self._pending_reading and time.monotonic() < self._pending_expires:
                self._pending_reading.changed_from = ""
                # Refresh the fields that can drift under a pending reading.
                self._pending_reading.data["dirty"] = dirty
                self._pending_reading.data["last_commit_ts"] = last_commit_ts
                self._pending_reading.data["last_commit_msg"] = last_commit_msg
                return self._pending_reading
            self._pending_reading = None
            return None

        parts = [f"On branch {branch}"]
        if new_commits:
            parts.append(f"new commits: {new_commits}")
        if dirty:
            parts.append("uncommitted changes")
        summary = ", ".join(parts)

        reading = self._reading(
            data={
                "head": head,
                "branch": branch,
                "dirty": dirty,
                "new_commits": new_commits,
                "last_commit_ts": last_commit_ts,
                "last_commit_msg": last_commit_msg,
            },
            summary=summary,
            changed_from=changed_from,
        )
        self._pending_reading = reading
        self._pending_expires = time.monotonic() + 120.0
        return reading

    async def teardown(self) -> None:
        pass

    async def _git(self, *args: str) -> str:
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip() if proc.returncode == 0 else ""
        except TimeoutError:
            if proc:
                proc.kill()
            return ""
        except Exception:
            return ""

    async def _is_dirty(self) -> bool:
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--quiet", "HEAD",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode != 0
        except TimeoutError:
            if proc:
                proc.kill()
            return False
        except Exception:
            return False
