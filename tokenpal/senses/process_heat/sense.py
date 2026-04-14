"""Process heat sense — names the top CPU hog when the machine is sustained-busy.

Transition-only: emits on trigger (sustained high CPU) and on clear. Filters
sensitive apps and aggregates Electron-family renderers by parent binary name.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, ClassVar

import psutil

from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

_TRIGGER_CPU_PCT = 80.0
_SUSTAINED_S = 20.0
_KERNEL_NAMES: frozenset[str] = frozenset({
    "kernel_task", "system", "systemd", "launchd",
    "idle", "swapper", "mds", "mds_stores", "windowserver",
})
_ELECTRON_HINT = re.compile(r"helper|renderer|gpu process", re.IGNORECASE)


def _friendly_name(proc_name: str) -> str:
    """Strip Electron-family suffixes so 'Slack Helper (Renderer)' becomes 'Slack'."""
    base = proc_name
    for sep in (" Helper", " helper"):
        if sep in base:
            base = base.split(sep, 1)[0]
    base = _ELECTRON_HINT.sub("", base).strip(" -()")
    return base or proc_name


@register_sense
class ProcessHeatSense(AbstractSense):
    sense_name: ClassVar[str] = "process_heat"
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    priority: ClassVar[int] = 100
    poll_interval_s: ClassVar[float] = 10.0
    reading_ttl_s: ClassVar[float] = 180.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._hot_since: float | None = None
        self._triggered: bool = False

    async def setup(self) -> None:
        psutil.cpu_percent(interval=None)
        # Prime per-process cpu counters too — without this, the first _top_hog()
        # after a hot-trigger reads 0.0 for every process.
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    async def poll(self) -> SenseReading | None:
        cpu = psutil.cpu_percent(interval=None)
        now = time.monotonic()
        hot = cpu >= _TRIGGER_CPU_PCT

        if hot:
            if self._hot_since is None:
                self._hot_since = now
            if self._triggered:
                return None
            if now - self._hot_since < _SUSTAINED_S:
                return None

            name, proc_cpu = self._top_hog()
            self._triggered = True

            if name is None:
                summary = f"CPU pinned at {cpu:.0f}% — can't identify the culprit"
            elif contains_sensitive_term(name):
                summary = f"CPU pinned at {cpu:.0f}% — something's working hard"
                name = None
            else:
                summary = (
                    f"CPU pinned at {cpu:.0f}% — {name} eating "
                    f"{proc_cpu:.0f}% on its own"
                )

            data: dict[str, Any] = {
                "cpu_percent": cpu,
                "top_process": name,
                "top_process_cpu": proc_cpu,
                "event": "hot",
            }
            return self._reading(data=data, summary=summary, confidence=3.0)

        # cooled off
        self._hot_since = None
        if self._triggered:
            self._triggered = False
            return self._reading(
                data={"cpu_percent": cpu, "event": "cool"},
                summary=f"CPU back to normal ({cpu:.0f}%)",
                confidence=1.5,
                changed_from="CPU pinned",
            )
        return None

    def _top_hog(self) -> tuple[str | None, float]:
        """Aggregate CPU% by friendly-name across Electron-family processes."""
        try:
            procs = list(psutil.process_iter(["name", "cpu_percent"]))
        except Exception:
            return None, 0.0

        bucket: dict[str, float] = {}
        for p in procs:
            info = p.info
            raw = (info.get("name") or "").strip()
            if not raw:
                continue
            if raw.lower() in _KERNEL_NAMES:
                continue
            pct = float(info.get("cpu_percent") or 0.0)
            if pct <= 0.0:
                continue
            key = _friendly_name(raw)
            bucket[key] = bucket.get(key, 0.0) + pct

        if not bucket:
            return None, 0.0
        name, total = max(bucket.items(), key=lambda kv: kv[1])
        # cpu_percent is per-core so aggregate can exceed 100%; cap for display.
        return name, min(total, 100.0)

    async def teardown(self) -> None:
        pass
