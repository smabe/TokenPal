"""Base class for LLM backends."""

from __future__ import annotations

import abc
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Result from a single LLM generation call."""

    text: str
    tokens_used: int
    model_name: str
    latency_ms: float
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_assistant_message(self) -> dict[str, Any]:
        """OpenAI-format assistant message for round-tripping back to the LLM.

        Substitutes ``call_{i}`` for empty tool_call_ids — Ollama sometimes
        returns empty strings which would cause the corresponding tool result
        message to be silently dropped.
        """
        return {
            "role": "assistant",
            "content": self.text or "",
            "tool_calls": [
                {
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for i, tc in enumerate(self.tool_calls)
            ],
        }


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

    @property
    def model_name(self) -> str:
        """Current model name."""
        return self._config.get("model_name", "unknown")

    @property
    def api_url(self) -> str:
        """Current API endpoint URL."""
        return self._config.get("api_url", "unknown")

    @property
    def is_reachable(self) -> bool:
        """Whether the backend is currently reachable."""
        return False

    @property
    def using_fallback(self) -> bool:
        """Whether the backend fell back to a secondary endpoint."""
        return False

    @property
    def primary_url(self) -> str:
        """The originally configured API endpoint (before any fallback)."""
        return self.api_url

    def set_model(self, model_name: str) -> None:
        """Swap the active model. Override in backends that support it."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support model swapping"
        )

    def set_api_url(self, url: str) -> None:
        """Switch the API endpoint at runtime. Override in backends that support it."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support URL switching"
        )

    @abc.abstractmethod
    async def setup(self) -> None:
        """Load model / connect to server."""

    @abc.abstractmethod
    async def generate(self, prompt: str, max_tokens: int | None = None) -> LLMResponse:
        """Single-shot generation. ``max_tokens=None`` uses the backend's configured default."""

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Chat completion with tool definitions. Default: fall back to generate()."""
        # Fallback for backends that don't support tools — just use the last user message
        prompt = messages[-1].get("content", "") if messages else ""
        return await self.generate(prompt, max_tokens)

    async def stream(self, prompt: str, max_tokens: int | None = None) -> AsyncIterator[str]:
        """Yield tokens as they arrive. Default: fall back to generate()."""
        response = await self.generate(prompt, max_tokens)
        yield response.text

    async def supports_vision(self) -> bool:
        """Override to return True if this backend handles image inputs."""
        return False

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Release resources."""
