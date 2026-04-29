"""Wedge ABC + registry. See CONTEXT.md."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar, Literal

from tokenpal.senses.base import SenseReading

if TYPE_CHECKING:
    from tokenpal.brain.personality import PersonalityEngine


LatencyBudget = Literal["observation", "freeform", "idle_tool", "tools"]


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


@dataclass(frozen=True)
class PromptContext:
    """Inputs the Brain hands a Wedge so it can build its prompt."""

    personality: PersonalityEngine
    snapshot: str


class Wedge(ABC):
    name: ClassVar[str]
    priority: ClassVar[int]
    gate: ClassVar[GatePolicy]
    latency_budget: ClassVar[LatencyBudget] = "observation"

    def ingest(self, readings: list[SenseReading]) -> None:
        pass

    @abstractmethod
    def propose(self) -> EmissionCandidate | None:
        ...

    @abstractmethod
    def build_prompt(self, candidate: EmissionCandidate, ctx: PromptContext) -> str:
        ...

    def on_emitted(self, candidate: EmissionCandidate, success: bool) -> None:
        """Notify the Wedge after the riff pipeline runs.

        Called on every post-LLM path (success, near-dup suppress, filter
        empty, sensitive-app block) but NOT on backend exception, so the
        wedge can retry next tick. `success` is True only when a bubble
        actually rendered — wedges whose cooldown should start only on a
        real emit gate on this; wedges that want to cool down on any
        post-LLM attempt ignore it.
        """



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
