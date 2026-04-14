"""System info action — report current CPU, RAM, disk, battery stats."""

from __future__ import annotations

from typing import Any

import psutil

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action


@register_action
class SystemInfoAction(AbstractAction):
    action_name = "system_info"
    description = "Get current system stats: CPU usage, RAM, disk space, and battery level."
    parameters = {
        "type": "object",
        "properties": {},
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        lines = [
            f"CPU: {cpu:.0f}%",
            f"RAM: {mem.percent:.0f}% ({mem.used / (1024**3):.1f}/{mem.total / (1024**3):.1f} GB)",
            f"Disk: {disk.percent:.0f}% "
            f"({disk.used / (1024**3):.0f}/{disk.total / (1024**3):.0f} GB)",
        ]

        battery = psutil.sensors_battery()
        if battery:
            plug = "plugged in" if battery.power_plugged else "on battery"
            lines.append(f"Battery: {battery.percent:.0f}% ({plug})")

        return ActionResult(output="; ".join(lines))
