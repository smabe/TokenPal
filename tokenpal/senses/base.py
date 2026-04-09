"""Base classes for all senses."""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class SenseReading:
    """One snapshot from a sense."""

    sense_name: str
    timestamp: float
    data: dict[str, Any]
    summary: str
    confidence: float = 1.0


class AbstractSense(abc.ABC):
    """Base class every sense must inherit from.

    Subclasses declare metadata as class variables for discovery:
        sense_name: identifier matching the config key (e.g. "app_awareness")
        platforms: tuple of supported platforms ("windows", "darwin", "linux")
        priority: lower = preferred when multiple impls exist for the same platform
    """

    sense_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]
    priority: ClassVar[int] = 100
    poll_interval_s: ClassVar[float] = 2.0

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._enabled = True

    @abc.abstractmethod
    async def setup(self) -> None:
        """One-time init. Called once at startup."""

    @abc.abstractmethod
    async def poll(self) -> SenseReading | None:
        """Return a reading, or None if nothing interesting happened."""

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Release resources."""

    def disable(self) -> None:
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _reading(self, data: dict[str, Any], summary: str, confidence: float = 1.0) -> SenseReading:
        """Helper to build a SenseReading with timestamp pre-filled."""
        return SenseReading(
            sense_name=self.sense_name,
            timestamp=time.monotonic(),
            data=data,
            summary=summary,
            confidence=confidence,
        )
