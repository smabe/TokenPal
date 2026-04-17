"""Productivity patterns sense — derives work patterns from MemoryStore data."""

from __future__ import annotations

import logging
import time
from typing import Any

from tokenpal.brain.personality import SENSITIVE_APPS
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Minimum session age before reporting anything meaningful
_MIN_SESSION_MINUTES = 5

# App categories for natural-language summaries
_APP_CATEGORIES: dict[str, list[str]] = {
    "code": ["vscode", "code", "xcode", "intellij", "pycharm", "vim", "nvim", "cursor", "zed"],
    "browser": ["chrome", "firefox", "safari", "arc", "brave", "edge"],
    "chat": ["slack", "discord", "telegram", "teams"],
    "terminal": ["terminal", "iterm", "ghostty", "warp", "alacritty", "kitty"],
    "creative": ["figma", "photoshop", "illustrator", "blender", "logic", "garageband"],
}

_SENSITIVE_APPS: set[str] = set(SENSITIVE_APPS)


def _categorize(app_name: str) -> str:
    lower = app_name.lower()
    for category, keywords in _APP_CATEGORIES.items():
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def _is_sensitive(app_name: str) -> bool:
    return app_name.lower() in _SENSITIVE_APPS


@register_sense
class ProductivityStats(AbstractSense):
    sense_name = "productivity"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 60.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._memory: Any = None  # MemoryStore, injected via config
        self._session_start: float = 0.0
        self._prev_summary: str = ""
        self._prev_buckets: tuple[str, str, str] | None = None

    async def setup(self) -> None:
        self._memory = self._config.get("memory_store")
        if self._memory is None:
            log.warning("Productivity sense has no MemoryStore — disabling")
            self.disable()
            return
        self._session_start = time.monotonic()

    async def poll(self) -> SenseReading | None:
        if not self.enabled or not self._memory:
            return None

        session_minutes = int((time.monotonic() - self._session_start) / 60)
        if session_minutes < _MIN_SESSION_MINUTES:
            return None

        try:
            stats = self._query_stats(session_minutes)
        except Exception:
            log.debug("Productivity query failed", exc_info=True)
            return None

        if not stats:
            return None

        summary = self._build_summary(stats)
        buckets = self._bucket_key(stats)

        # Emit with full confidence only on bucket transition — integer drift
        # inside the same bucket is structurally boring and causes the topic
        # roulette to favor productivity forever.
        confidence = 1.0 if buckets != self._prev_buckets else 0.0
        self._prev_buckets = buckets
        self._prev_summary = summary

        return self._reading(data=stats, summary=summary, confidence=confidence)

    @staticmethod
    def _bucket_key(stats: dict[str, Any]) -> tuple[str, str, str]:
        """Hysteresis buckets — changes drive emission, integer drift does not."""
        time_min = stats["time_in_current_min"]
        switches = stats["switches_per_hour"]
        streak = stats["longest_streak_min"]

        if time_min >= 30:
            time_bucket = "deep_focus"
        elif time_min >= 10:
            time_bucket = "settled"
        else:
            time_bucket = "just_arrived"

        if switches >= 15:
            switch_bucket = "restless"
        elif switches >= 8:
            switch_bucket = "active"
        else:
            switch_bucket = "calm"

        streak_bucket = "long_streak" if streak >= 30 else "none"

        return (time_bucket, switch_bucket, streak_bucket)

    def _query_stats(self, session_minutes: int) -> dict[str, Any]:
        """Query MemoryStore for current session productivity metrics."""
        conn = self._memory._conn
        if not conn:
            return {}
        session_id = self._memory.session_id
        lock = self._memory._lock

        with lock:
            # Switches this session
            rows = conn.execute(
                "SELECT summary, timestamp FROM observations "
                "WHERE session_id = ? AND event_type = 'app_switch' "
                "ORDER BY timestamp",
                (session_id,),
            ).fetchall()

        if not rows:
            return {}

        total_switches = len(rows)
        switches_per_hour = total_switches / max(session_minutes / 60, 1 / 60)

        # Current app and time-in-current
        current_app = rows[-1][0]
        current_start = rows[-1][1]
        time_in_current_min = int((time.time() - current_start) / 60)

        # Longest streak (max gap between consecutive switches for same app)
        longest_streak_min = 0
        for i in range(len(rows)):
            start = rows[i][1]
            end = rows[i + 1][1] if i + 1 < len(rows) else time.time()
            duration = int((end - start) / 60)
            if duration > longest_streak_min:
                longest_streak_min = duration

        category = _categorize(current_app)

        return {
            "current_app": current_app,
            "category": category,
            "time_in_current_min": time_in_current_min,
            "switches_per_hour": round(switches_per_hour, 1),
            "longest_streak_min": longest_streak_min,
            "total_switches": total_switches,
            "session_minutes": session_minutes,
        }

    def _build_summary(self, stats: dict[str, Any]) -> str:
        """Build a bucketed natural-language summary.

        Integer values are intentionally omitted from the summary string —
        they drift every poll and would make the topic roulette's
        change_bonus re-fire endlessly. The LLM still sees the raw numbers
        via the data dict when needed.
        """
        parts: list[str] = []
        app = stats["current_app"]
        time_bucket, switch_bucket, streak_bucket = self._bucket_key(stats)

        app_label = app if not _is_sensitive(app) else "a private app"

        if time_bucket == "deep_focus":
            parts.append(f"Deep focus in {app_label}")
        elif time_bucket == "settled":
            parts.append(f"Settled into {app_label}")

        if switch_bucket == "restless":
            parts.append("very restless — bouncing between apps")
        elif switch_bucket == "active":
            parts.append("active multitasking across apps")

        if streak_bucket == "long_streak" and not parts:
            parts.append("long focus streak")

        if not parts:
            return "Working, calm pace"

        return ", ".join(parts)

    async def teardown(self) -> None:
        pass
