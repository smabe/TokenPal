"""Proactive WIP-commit nudge.

Fires when ALL of:
 1. HEAD commit message matches one of ``wip_markers`` (case-insensitive
    substring — "wip", "tmp", "todo", "fixup!" by default). This is our
    proxy for "commit the user intended to amend / follow up on."
 2. The working tree is dirty (uncommitted changes present).
 3. ``wip_stale_hours`` have elapsed since the WIP commit landed.
 4. The user is still present (has had at least one sense reading this
    session — the orchestrator passes ``user_present`` to ``check``).

Internal cooldown keeps the detector silent for ``cooldown_s`` after each
emit. Consumes git sense readings for live state; on startup the caller
is expected to run ``hydrate`` to pull initial state without waiting for
a git change.

See plans/buddy-utility-wedges.md.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass

from tokenpal.config.schema import GitNudgeConfig
from tokenpal.senses.base import SenseReading

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitNudgeSignal:
    branch: str
    last_commit_msg: str
    stale_hours: float


class GitNudgeDetector:
    """Consumes git readings; emits a nudge when WIP has been stale too long."""

    def __init__(self, config: GitNudgeConfig) -> None:
        self._config = config
        self._branch: str = ""
        self._dirty: bool = False
        self._last_commit_ts: float | None = None
        self._last_commit_msg: str = ""
        self._last_emit_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def hydrate(self) -> None:
        """One-shot initial state probe via git CLI.

        The git sense only emits readings on change, so on a fresh session
        the detector would have no state until the user next commits or
        switches branch. This lets a WIP branch that was already stale at
        startup still get a nudge.
        """
        if not shutil.which("git"):
            return
        try:
            branch, dirty_code, commit_ts_raw, commit_msg = await asyncio.gather(
                _git("rev-parse", "--abbrev-ref", "HEAD"),
                _git_exit_code("diff", "--quiet", "HEAD"),
                _git("log", "-1", "--format=%ct"),
                _git("log", "-1", "--format=%s"),
            )
        except Exception:
            log.debug("git_nudge hydrate failed", exc_info=True)
            return
        self._branch = branch
        self._dirty = dirty_code != 0
        try:
            self._last_commit_ts = float(commit_ts_raw)
        except (ValueError, TypeError):
            self._last_commit_ts = None
        self._last_commit_msg = commit_msg

    def ingest(self, readings: list[SenseReading]) -> None:
        """Update cached state from any git readings in this tick."""
        for r in readings:
            if r.sense_name != "git":
                continue
            data = r.data or {}
            if "branch" in data:
                self._branch = str(data["branch"])
            if "dirty" in data:
                self._dirty = bool(data["dirty"])
            if "last_commit_ts" in data and data["last_commit_ts"] is not None:
                try:
                    self._last_commit_ts = float(data["last_commit_ts"])
                except (ValueError, TypeError):
                    pass
            if "last_commit_msg" in data:
                self._last_commit_msg = str(data["last_commit_msg"])

    def check(self, user_present: bool) -> GitNudgeSignal | None:
        if not self._config.enabled:
            return None
        if not user_present:
            return None
        if not self._dirty:
            return None
        if self._last_commit_ts is None or not self._last_commit_msg:
            return None
        if not self._is_wip(self._last_commit_msg):
            return None
        stale_s = time.time() - self._last_commit_ts
        if stale_s < self._config.wip_stale_hours * 3600:
            return None
        if (time.monotonic() - self._last_emit_at) < self._config.cooldown_s:
            return None
        return GitNudgeSignal(
            branch=self._branch,
            last_commit_msg=self._last_commit_msg,
            stale_hours=stale_s / 3600,
        )

    def mark_emitted(self) -> None:
        self._last_emit_at = time.monotonic()

    def _is_wip(self, msg: str) -> bool:
        lower = msg.lower()
        return any(m.lower() in lower for m in self._config.wip_markers)


# ---------------------------------------------------------------------------
# Helpers — small async git CLI wrappers local to this module. Duplicates
# the pattern in tokenpal/senses/git/git_sense.py to keep the dependency
# surface small and avoid a circular import.
# ---------------------------------------------------------------------------


async def _git(*args: str) -> str:
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


async def _git_exit_code(*args: str) -> int:
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return int(proc.returncode or 0)
    except TimeoutError:
        if proc:
            proc.kill()
        return 0
    except Exception:
        return 0
