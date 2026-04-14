"""Windows music detection stub — future implementation via winrt-Windows.Media.Control (SMTC)."""

from __future__ import annotations

from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense


@register_sense
class WindowsMusic(AbstractSense):
    sense_name = "music"
    platforms = ("windows",)
    priority = 100
    poll_interval_s = 15.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

    async def setup(self) -> None:
        # Stub: full implementation would use winrt-Windows.Media.Control
        # to query the System Media Transport Controls (SMTC) for now-playing info.
        self.disable()

    async def poll(self) -> SenseReading | None:
        return None

    async def teardown(self) -> None:
        pass
