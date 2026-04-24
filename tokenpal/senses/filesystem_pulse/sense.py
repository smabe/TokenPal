"""Filesystem pulse sense — detects activity bursts in watched directories.

Uses watchdog to observe file-modify/create/move events in user-configured
roots. Emits a reading when >= _BURST_THRESHOLD events land in a single root
within _BURST_WINDOW_S seconds. One reading per burst per root — the same
root won't re-emit until it has quieted down for at least _BURST_COOLDOWN_S.

Privacy: we emit only the leaf directory name (`tokenpal`, not
`~/projects/work/acme/tokenpal`). Individual filenames and paths are
never included in summaries, log lines, or reading data fields.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

_BURST_WINDOW_S = 30.0    # events within this window count toward a burst
_BURST_THRESHOLD = 5      # N events in the window -> burst reading
_BURST_COOLDOWN_S = 60.0  # minimum gap between burst readings for the same root

# Directories we never descend into — generate high event volume with zero signal.
_EXCLUDED_DIR_NAMES = frozenset({
    "node_modules", ".venv", "venv", ".git", "__pycache__",
    "build", "dist", "target", ".next", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "Pods", "DerivedData",
})


def _is_excluded_path(path: str) -> bool:
    return any(part in _EXCLUDED_DIR_NAMES for part in Path(path).parts)


@register_sense
class FilesystemPulse(AbstractSense):
    sense_name = "filesystem_pulse"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 2.0
    reading_ttl_s = 90.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self._observer: Any = None
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._last_burst: dict[str, float] = {}
        self._root_leaf: dict[str, str] = {}
        self._pending_bursts: list[tuple[str, int]] = []

    async def setup(self) -> None:
        from tokenpal.config.paths import default_watch_roots

        raw_roots = self._config.get("roots") or []
        if raw_roots:
            roots = [Path(r).expanduser().resolve() for r in raw_roots]
        else:
            roots = list(default_watch_roots())
        roots = [r for r in roots if r.is_dir()]

        if not roots:
            log.warning("filesystem_pulse: no watchable roots — disabling sense")
            self.disable()
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warning("watchdog not installed — disabling filesystem_pulse")
            self.disable()
            return

        handler = _make_handler(self, FileSystemEventHandler)
        self._observer = Observer()
        for root in roots:
            key = str(root)
            leaf = root.name or "root"
            self._root_leaf[key] = leaf
            self._observer.schedule(handler, key, recursive=True)
            log.info("filesystem_pulse: watching %s", leaf)
        self._observer.start()

    async def teardown(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    def _record_event(self, src_path: str) -> None:
        """Called from the watchdog thread. Must be fast + thread-safe."""
        if _is_excluded_path(src_path):
            return
        root_key = self._root_for(src_path)
        if root_key is None:
            return

        now = time.monotonic()
        with self._lock:
            q = self._events[root_key]
            q.append(now)
            cutoff = now - _BURST_WINDOW_S
            while q and q[0] < cutoff:
                q.popleft()

            last = self._last_burst.get(root_key, 0.0)
            if len(q) >= _BURST_THRESHOLD and now - last >= _BURST_COOLDOWN_S:
                self._last_burst[root_key] = now
                # Cap pending queue so a stalled poll loop can't leak unboundedly.
                if len(self._pending_bursts) < 2 * max(len(self._root_leaf), 1):
                    self._pending_bursts.append((root_key, len(q)))
                q.clear()

    def _root_for(self, src_path: str) -> str | None:
        """Return the watched root containing *src_path*, or None.

        Compares with a path-separator boundary so `/foo/Downloads` does not
        match `/foo/Downloads2/x`.
        """
        for root_key in self._root_leaf:
            if src_path == root_key or src_path.startswith(root_key + os.sep):
                return root_key
        return None

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        with self._lock:
            if not self._pending_bursts:
                return None
            root_key, count = self._pending_bursts.pop(0)

        leaf = self._root_leaf.get(root_key, "a watched folder")
        return self._reading(
            data={"event": "activity_burst", "root": leaf, "count": count},
            summary=f"Activity in {leaf} ({count} file events in 30s)",
            confidence=0.7,
        )


def _make_handler(sense: FilesystemPulse, base_cls: type) -> Any:
    """Build a watchdog handler that forwards events to the sense.

    Subclasses FileSystemEventHandler so watchdog's Observer accepts it;
    we override dispatch to handle all event kinds uniformly.
    """

    class _Handler(base_cls):  # type: ignore[misc]
        def dispatch(self, event: Any) -> None:
            if getattr(event, "is_directory", False):
                return
            src = getattr(event, "src_path", "") or ""
            if src:
                sense._record_event(src)

    return _Handler()
