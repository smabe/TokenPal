"""HTTP backend — talks to any OpenAI-compatible local API (Ollama, LM Studio, Foundry)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from tokenpal.config.schema import InferenceEngine
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse, ToolCall
from tokenpal.llm.registry import register_backend

log = logging.getLogger(__name__)


@register_backend
class HttpBackend(AbstractLLMBackend):
    backend_name = "http"
    platforms = ("windows", "darwin", "linux")

    _FALLBACK_URL = "http://localhost:11434/v1"
    _LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")
    _MAX_TOKENS_HARD_CAP = 1024
    _PROBE_TIMEOUT_S = 5.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._api_url = config.get("api_url", "http://localhost:11434/v1")
        self._primary_url = self._api_url  # remember the configured URL for retry
        self._model_name = config.get("model_name", "phi3:mini")
        self._temperature = config.get("temperature", 0.8)
        self._disable_reasoning = config.get("disable_reasoning", True)
        self._inference_engine: InferenceEngine = config.get("inference_engine", "ollama")
        self._server_mode = config.get("server_mode", "auto")
        self._initial_max_tokens: int = int(config.get("max_tokens", 256))
        self._max_tokens: int = self._initial_max_tokens
        # Resolve per-server overrides (populated by /model and /server switch)
        from tokenpal.config.toml_writer import canon_server_url

        key = canon_server_url(self._api_url)
        per_model: dict[str, str] = config.get("per_server_models") or {}
        if key in per_model:
            self._model_name = per_model[key]
        self._per_server_max_tokens: dict[str, int] = (
            config.get("per_server_max_tokens") or {}
        )
        self._max_tokens_pinned: bool = key in self._per_server_max_tokens
        if self._max_tokens_pinned:
            self._max_tokens = int(self._per_server_max_tokens[key])
        self._derived_max_tokens: int | None = None
        self._context_length: int | None = None
        self._client: httpx.AsyncClient | None = None
        self._reachable: bool = False
        self._model_available: bool = False
        self._using_fallback: bool = False

    async def _try_connect(self, url: str, *, allow_adopt: bool = True) -> bool:
        """Try connecting to an API endpoint. Returns True on success.

        When *allow_adopt* is True and the configured model_name is not in the
        server's model list, auto-adopt the first advertised model so clients
        track server-side model swaps without manual config edits. Disabled on
        the fallback path to avoid adopting random local Ollama models.
        """
        assert self._client is not None
        try:
            resp = await self._client.get(f"{url}/models")
            resp.raise_for_status()
            self._api_url = url
            self._reachable = True

            models = resp.json().get("data", [])
            model_ids = [m.get("id", "") for m in models if m.get("id")]

            if self._model_name in model_ids:
                self._model_available = True
                log.info("Model '%s' is available", self._model_name)
            elif model_ids and allow_adopt:
                from tokenpal.config.toml_writer import canon_server_url

                key = canon_server_url(url)
                per_model: dict[str, str] = self._config.get("per_server_models") or {}
                if key not in per_model:
                    old = self._model_name
                    self._model_name = model_ids[0]
                    self._model_available = True
                    log.info(
                        "Server advertises '%s' (was '%s') -- auto-adopted",
                        self._model_name, old,
                    )
                else:
                    log.warning(
                        "Model '%s' not found on server. Available: %s",
                        self._model_name, ", ".join(model_ids),
                    )
            elif model_ids:
                log.warning(
                    "Model '%s' not found on fallback. Available: %s",
                    self._model_name, ", ".join(model_ids),
                )
            else:
                log.warning("No models found on server at %s", url)
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
            await self._apply_auto_max_tokens()
            return

        is_local = any(h in self._api_url for h in self._LOCAL_HOSTS)
        if self._server_mode == "auto" and not is_local:
            log.warning(
                "Cannot reach %s — trying local Ollama fallback...", self._api_url,
            )
            if await self._try_connect(self._FALLBACK_URL, allow_adopt=False):
                self._using_fallback = True
                log.info(
                    "Using local Ollama fallback at %s (remote unreachable)",
                    self._FALLBACK_URL,
                )
                await self._apply_auto_max_tokens()
                return

        log.warning(
            "Could not reach LLM API at %s — start Ollama with: ollama serve",
            self._api_url,
        )

    def _apply_thinking_controls(
        self, body: dict[str, Any], enable_thinking: bool | None
    ) -> None:
        """Write the per-engine thinking controls into the request body.

        `reasoning_effort` is inert on Qwen3 via llama-server; the real knob is
        `chat_template_kwargs.enable_thinking` (server-side merge lets it win
        over the `--reasoning off` startup default). `reasoning_format=deepseek`
        routes thinking tokens to a separate `reasoning_content` response field
        so callers that request JSON get a clean `content`.
        """
        effective = (
            enable_thinking
            if enable_thinking is not None
            else not self._disable_reasoning
        )
        if self._inference_engine == "llamacpp":
            body["chat_template_kwargs"] = {
                "enable_thinking": "true" if effective else "false"
            }
            body["reasoning_format"] = "deepseek"
        else:
            body["reasoning_effort"] = "high" if effective else "none"

    async def generate(
        self,
        prompt: str,
        max_tokens: int | None = None,
        *,
        enable_thinking: bool | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        effective_max = self._max_tokens if max_tokens is None else max_tokens
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": effective_max,
            "temperature": self._temperature,
        }
        self._apply_thinking_controls(body, enable_thinking)
        if response_format is not None:
            body["response_format"] = response_format

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - start) * 1000

        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason")
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens,
            model_name=self._model_name,
            latency_ms=elapsed_ms,
            finish_reason=finish_reason,
        )

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        *,
        enable_thinking: bool | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        effective_max = self._max_tokens if max_tokens is None else max_tokens
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": effective_max,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools
        self._apply_thinking_controls(body, enable_thinking)
        if response_format is not None:
            body["response_format"] = response_format

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - start) * 1000

        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
        finish_reason = choice.get("finish_reason")
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
            finish_reason=finish_reason,
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
        # Capability is model-specific; drop cached probe so refresh_capability re-probes.
        self._derived_max_tokens = None
        self._context_length = None
        if not self._max_tokens_pinned:
            self._max_tokens = self._initial_max_tokens
        log.info("Model swapped to: %s", model_name)

    def set_max_tokens(self, n: int) -> None:
        """Swap the default max_tokens cap. Affects calls that don't pass one explicitly."""
        self._max_tokens = int(n)
        self._max_tokens_pinned = True
        log.info("Default max_tokens set to: %d (pinned)", self._max_tokens)

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def derived_max_tokens(self) -> int | None:
        """The auto-derived cap from the last capability probe, if any."""
        return self._derived_max_tokens

    @property
    def context_length(self) -> int | None:
        """Model context length reported by the last capability probe, if any."""
        return self._context_length

    def set_api_url(self, url: str) -> None:
        """Switch the API endpoint at runtime. Used by /server switch."""
        from tokenpal.config.toml_writer import canon_server_url

        self._api_url = url.rstrip("/")
        self._primary_url = self._api_url
        self._reachable = False
        self._model_available = False
        self._using_fallback = False
        self._derived_max_tokens = None
        self._context_length = None
        # Re-evaluate pin status against the new server's entry in config.
        key = canon_server_url(self._api_url)
        if key in self._per_server_max_tokens:
            self._max_tokens = int(self._per_server_max_tokens[key])
            self._max_tokens_pinned = True
        else:
            self._max_tokens_pinned = False
            self._max_tokens = self._initial_max_tokens
        log.info("API URL switched to: %s", self._api_url)

    async def _probe_context_length(self) -> int | None:
        """Probe Ollama's native /api/show for the active model's context_length.

        Returns None on any error (non-Ollama backend, network failure, missing field).
        """
        assert self._client is not None
        native_root = self._api_url
        if native_root.endswith("/v1"):
            native_root = native_root[:-3]
        try:
            resp = await self._client.post(
                f"{native_root}/api/show",
                json={"name": self._model_name},
                timeout=self._PROBE_TIMEOUT_S,
            )
            resp.raise_for_status()
            model_info = resp.json().get("model_info") or {}
            lengths = [
                int(v) for k, v in model_info.items()
                if k.endswith(".context_length") and isinstance(v, (int, float))
            ]
            if not lengths:
                return None
            return max(lengths)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.debug("Capability probe failed for %s: %s", self._model_name, e)
            return None

    async def _apply_auto_max_tokens(self) -> None:
        """Probe server capability and update max_tokens when not user-pinned."""
        ctx = await self._probe_context_length()
        if ctx is None:
            return
        self._context_length = ctx
        derived = min(ctx // 4, self._MAX_TOKENS_HARD_CAP)
        self._derived_max_tokens = derived
        if self._max_tokens_pinned:
            log.info(
                "Auto-derived max_tokens=%d from context_length=%d (%s @ %s) "
                "— not applied (user-pinned at %d)",
                derived, ctx, self._model_name, self._api_url, self._max_tokens,
            )
            return
        self._max_tokens = derived
        log.info(
            "Auto-derived max_tokens=%d from context_length=%d (%s @ %s)",
            derived, ctx, self._model_name, self._api_url,
        )

    async def refresh_capability(self) -> None:
        """Re-probe capability after a model swap without a full setup()."""
        if self._client is None:
            return
        await self._apply_auto_max_tokens()

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
