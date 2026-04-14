"""Slash command dispatcher for interactive TokenPal commands."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result from a slash command."""

    message: str
    error: str | None = None


class CommandDispatcher:
    """Registry and dispatcher for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Callable[[str], CommandResult]] = {}

    def register(
        self, name: str, handler: Callable[[str], CommandResult]
    ) -> None:
        self._commands[name] = handler

    def dispatch(self, raw_input: str) -> CommandResult:
        """Parse and dispatch a slash command. Returns a result to display."""
        parts = raw_input.lstrip("/").split(maxsplit=1)
        name = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(name)
        if handler is None:
            available = ", ".join(f"/{c}" for c in sorted(self._commands))
            return CommandResult(f"Unknown: /{name}. Try: {available}")

        try:
            return handler(args)
        except Exception as e:
            log.exception("Command /%s failed", name)
            return CommandResult(f"Error: {e}")

    def help_text(self) -> str:
        """List all registered commands."""
        return " | ".join(f"/{c}" for c in sorted(self._commands))
