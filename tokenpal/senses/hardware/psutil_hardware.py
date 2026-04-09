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
    poll_interval_s = 10.0

    async def setup(self) -> None:
        # Prime the cpu_percent counter (first call always returns 0)
        psutil.cpu_percent(interval=None)

    async def poll(self) -> SenseReading | None:
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()

        # Expressive summaries based on utilization level
        if cpu >= 90:
            cpu_str = f"CPU {cpu:.0f}% — something is melting the processor"
        elif cpu >= 70:
            cpu_str = f"CPU {cpu:.0f}% — working hard"
        else:
            cpu_str = f"CPU {cpu:.0f}%"

        if mem.percent >= 90:
            ram_str = f"RAM {mem.percent:.0f}% — nearly full, things might start dying"
        elif mem.percent >= 75:
            ram_str = f"RAM {mem.percent:.0f}% — getting crowded"
        else:
            ram_str = f"RAM {mem.percent:.0f}%"

        parts = [cpu_str, ram_str]

        battery = psutil.sensors_battery()
        if battery is not None:
            if not battery.power_plugged and battery.percent <= 10:
                parts.append(f"Battery {battery.percent:.0f}% — DYING")
            elif not battery.power_plugged and battery.percent <= 20:
                parts.append(f"Battery {battery.percent:.0f}% — low, not plugged in")
            else:
                plug = "plugged in" if battery.power_plugged else "on battery"
                parts.append(f"Battery {battery.percent:.0f}% ({plug})")

        summary = ", ".join(parts)

        # Higher confidence when hardware is stressed — makes it more
        # likely to trigger a comment through the interestingness gate
        confidence = 1.0
        if cpu < 70 and mem.percent < 75:
            confidence = 1.0  # normal — weight (0.1) keeps it low anyway
        if cpu >= 70 or mem.percent >= 75:
            confidence = 3.0  # stressed — pushes past threshold (0.1 * 3.0 = 0.3)
        if cpu >= 90 or mem.percent >= 90:
            confidence = 5.0  # critical — definitely comment (0.1 * 5.0 = 0.5)

        data: dict[str, Any] = {
            "cpu_percent": cpu,
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / (1024**3), 1),
            "ram_total_gb": round(mem.total / (1024**3), 1),
        }
        if battery is not None:
            data["battery_percent"] = battery.percent
            data["power_plugged"] = battery.power_plugged

        return self._reading(data=data, summary=summary, confidence=confidence)

    async def teardown(self) -> None:
        pass
