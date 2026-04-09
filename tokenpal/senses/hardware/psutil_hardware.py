"""Cross-platform hardware monitoring via psutil."""

from __future__ import annotations

import logging
from typing import Any

import psutil

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)


@register_sense
class PsutilHardware(AbstractSense):
    sense_name = "hardware"
    platforms = ("windows", "darwin", "linux")
    priority = 200  # generic fallback; platform-specific ones get lower priority

    async def setup(self) -> None:
        # Prime the cpu_percent counter (first call always returns 0)
        psutil.cpu_percent(interval=None)

    async def poll(self) -> SenseReading | None:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()

        parts = [f"CPU {cpu:.0f}%", f"RAM {mem.percent:.0f}%"]

        battery = psutil.sensors_battery()
        if battery is not None:
            plug = "plugged in" if battery.power_plugged else "on battery"
            parts.append(f"Battery {battery.percent:.0f}% ({plug})")

        summary = ", ".join(parts)

        data: dict[str, Any] = {
            "cpu_percent": cpu,
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / (1024**3), 1),
            "ram_total_gb": round(mem.total / (1024**3), 1),
        }
        if battery is not None:
            data["battery_percent"] = battery.percent
            data["power_plugged"] = battery.power_plugged

        return self._reading(data=data, summary=summary)

    async def teardown(self) -> None:
        pass
