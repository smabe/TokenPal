"""Time awareness sense — time of day, day of week, session duration."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense


@register_sense
class TimeSense(AbstractSense):
    sense_name = "time_awareness"
    platforms = ("windows", "darwin", "linux")
    priority = 100

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._session_start: float = 0.0

    async def setup(self) -> None:
        self._session_start = time.monotonic()

    async def poll(self) -> SenseReading | None:
        now = datetime.now()
        elapsed = time.monotonic() - self._session_start
        hours, remainder = divmod(int(elapsed), 3600)
        minutes = remainder // 60

        time_str = now.strftime("%I:%M %p").lstrip("0")
        day_str = now.strftime("%A")

        if hours > 0:
            session_str = f"{hours}h {minutes}m"
        else:
            session_str = f"{minutes}m"

        summary = f"It's {time_str} on {day_str}, user has been working for {session_str}"

        return self._reading(
            data={
                "time": now.isoformat(),
                "day_of_week": day_str,
                "hour": now.hour,
                "session_minutes": int(elapsed / 60),
            },
            summary=summary,
        )

    async def teardown(self) -> None:
        pass
