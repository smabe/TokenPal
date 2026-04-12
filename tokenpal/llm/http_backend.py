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

    _FALLBACK_URL = "http://localhost:11434/v1"
    _LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._api_url = config.get("api_url", "http://localhost:11434/v1")
        self._primary_url = self._api_url  # remember the configured URL for retry
        self._model_name = config.get("model_name", "phi3:mini")
        self._temperature = config.get("temperature", 0.8)
        self._disable_reasoning = config.get("disable_reasoning", True)
        self._server_mode = config.get("server_mode", "auto")
        self._client: httpx.AsyncClient | None = None
        self._reachable: bool = False
        self._model_available: bool = False
        self._using_fallback: bool = False

    async def _try_connect(self, url: str) -> bool:
        """Try connecting to an API endpoint. Returns True on success."""
        assert self._client is not None
        try:
            resp = await self._client.get(f"{url}/models")
            resp.raise_for_status()
            self._api_url = url
            self._reachable = True

            models = resp.json().get("data", [])
            model_ids = {m.get("id", "") for m in models}
            if self._model_name in model_ids:
                self._model_available = True
                log.info("Model '%s' is available", self._model_name)
            else:
                log.warning(
                    "Model '%s' not found. Run: ollama pull %s",
                    self._model_name,
                    self._model_name,
                )
            return True
        except httpx.HTTPError:
            return False

    async def setup(self) -> None:
        if self._client:
            await self._client.aclose()
        self._client = httpx.AsyncClient(timeout=60.0)
        self._reachable = False
        self._model_available = False
        self._using_fallback = False

        if await self._try_connect(self._api_url):
            log.info("Connected to LLM API at %s", self._api_url)
            return

        is_local = any(h in self._api_url for h in self._LOCAL_HOSTS)
        if self._server_mode == "auto" and not is_local:
            log.warning(
                "Cannot reach %s — trying local Ollama fallback...", self._api_url,
            )
            if await self._try_connect(self._FALLBACK_URL):
                self._using_fallback = True
                log.info(
                    "Using local Ollama fallback at %s (remote unreachable)",
                    self._FALLBACK_URL,
                )
                return

        log.warning(
            "Could not reach LLM API at %s — start Ollama with: ollama serve",
            self._api_url,
        )

    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }
        # Disable thinking for models that support it — we want fast, short quips
        if self._disable_reasoning:
            body["reasoning_effort"] = "none"

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - start) * 1000

        text = data["choices"][0]["message"].get("content") or ""
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
        if self._disable_reasoning:
            body["reasoning_effort"] = "none"

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

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def api_url(self) -> str:
        return self._api_url

    @property
    def is_reachable(self) -> bool:
        return self._reachable

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    @property
    def primary_url(self) -> str:
        return self._primary_url

    def set_model(self, model_name: str) -> None:
        """Swap the active model. Next generation call uses the new model."""
        self._model_name = model_name
        log.info("Model swapped to: %s", model_name)

    def set_api_url(self, url: str) -> None:
        """Switch the API endpoint at runtime. Used by /server switch."""
        self._api_url = url.rstrip("/")
        self._primary_url = self._api_url
        self._reachable = False
        self._model_available = False
        self._using_fallback = False
        log.info("API URL switched to: %s", self._api_url)

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
