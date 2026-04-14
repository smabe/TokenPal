"""Base class for LLM-callable actions (tools)."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class ActionResult:
    """Result from executing an action."""

    output: str
    success: bool = True


class AbstractAction(abc.ABC):
    """Base class every LLM-callable action must inherit from.

    Subclasses declare metadata as class variables for discovery:
        action_name: identifier used in tool definitions sent to the LLM
        description: one-line description the LLM sees to decide when to call it
        parameters: JSON Schema dict describing accepted arguments
        platforms: tuple of supported platforms ("windows", "darwin", "linux")
    """

    action_name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    # Gate flags for future autonomous tool-calling. `safe` = no side effects
    # beyond reading state; `requires_confirm` = host must prompt the user
    # before the LLM can invoke this unattended.
    safe: ClassVar[bool] = False
    requires_confirm: ClassVar[bool] = True

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abc.abstractmethod
    async def execute(self, **kwargs: Any) -> ActionResult:
        """Run the action with the given arguments. Must be safe and bounded."""

    async def teardown(self) -> None:
        """Release resources. Override if the action holds async state."""

    def to_tool_spec(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible tool definition for the LLM."""
        return {
            "type": "function",
            "function": {
                "name": self.action_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
