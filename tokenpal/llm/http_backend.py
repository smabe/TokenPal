"""HTTP backend — talks to any OpenAI-compatible local API (Ollama, LM Studio, Foundry)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall
from tokenpal.llm.registry import register_backend

log = logging.getLogger(__name__)


@register_backend
class HttpBackend(AbstractLLMBackend):
    backend_name = "http"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._api_url = config.get("api_url", "http://localhost:11434/v1")
        self._model_name = config.get("model_name", "phi3:mini")
        self._temperature = config.get("temperature", 0.8)
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        self._client = httpx.AsyncClient(timeout=60.0)
        # Quick health check
        try:
            resp = await self._client.get(f"{self._api_url}/models")
            resp.raise_for_status()
            log.info("Connected to LLM API at %s", self._api_url)
        except httpx.HTTPError:
            log.warning(
                "Could not reach LLM API at %s — make sure Ollama/LM Studio is running",
                self._api_url,
            )

    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json={
                "model": self._model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": self._temperature,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - start) * 1000

        text = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens,
            model_name=self._model_name,
            latency_ms=elapsed_ms,
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 256,
    ) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - start) * 1000

        message = data["choices"][0]["message"]
        text = message.get("content") or ""
        tokens = data.get("usage", {}).get("total_tokens", 0)

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                log.warning("Bad tool call arguments: %s", raw_args)
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
            ))

        return LLMResponse(
            text=text,
            tokens_used=tokens,
            model_name=self._model_name,
            latency_ms=elapsed_ms,
            tool_calls=tool_calls,
        )

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
