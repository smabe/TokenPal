"""Wedge ABC + registry. See CONTEXT.md."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar

from tokenpal.senses.base import SenseReading


class GatePolicy(Enum):
    BYPASS_CAP = auto()
    NEEDS_CAP_OPEN = auto()
    IDLE_FILL = auto()


@dataclass(frozen=True)
class EmissionCandidate:
    """A Wedge's proposal to fire this tick.

    The payload is opaque: only the originating Wedge knows its type.
    Priority and gate policy are read from the Wedge class.
    """

    wedge_name: str
    payload: object


class Wedge(ABC):
    name: ClassVar[str]
    priority: ClassVar[int]
    gate: ClassVar[GatePolicy]

    def ingest(self, readings: list[SenseReading]) -> None:
        pass

    @abstractmethod
    def propose(self, now: float) -> EmissionCandidate | None:
        ...


class WedgeRegistry:
    def __init__(self) -> None:
        self._wedges: dict[str, Wedge] = {}

    def register(self, wedge: Wedge) -> None:
        if wedge.name in self._wedges:
            raise ValueError(f"Wedge {wedge.name!r} already registered")
        self._wedges[wedge.name] = wedge

    def __iter__(self) -> Iterator[Wedge]:
        return iter(self._wedges.values())

    def __len__(self) -> int:
        return len(self._wedges)
