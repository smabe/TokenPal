"""Base class for LLM backends."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class LLMResponse:
    """Result from a single LLM generation call."""

    text: str
    tokens_used: int
    model_name: str
    latency_ms: float


class AbstractLLMBackend(abc.ABC):
    """Base class for all LLM backends.

    Subclasses declare:
        backend_name: matches config llm.backend value (e.g. "http", "mlx")
        platforms: tuple of supported platforms
    """

    backend_name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]]

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abc.abstractmethod
    async def setup(self) -> None:
        """Load model / connect to server."""

    @abc.abstractmethod
    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse:
        """Single-shot generation."""

    async def stream(self, prompt: str, max_tokens: int = 256) -> AsyncIterator[str]:
        """Yield tokens as they arrive. Default: fall back to generate()."""
        response = await self.generate(prompt, max_tokens)
        yield response.text

    async def supports_vision(self) -> bool:
        """Override to return True if this backend handles image inputs."""
        return False

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Release resources."""
