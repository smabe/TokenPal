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
    # Throughput estimator — see plans/gpu-scaling.md.
    _EWMA_ALPHA = 0.2
    _MIN_SAMPLES_FOR_ESTIMATE = 3

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
        # Throughput estimator state. None until MIN_SAMPLES_FOR_ESTIMATE calls
        # accumulate. See plans/gpu-scaling.md for the TTFT/decode split.
        self._target_latency_scaling: bool = bool(
            config.get("target_latency_scaling", False)
        )
        self._sample_count: int = 0
        self._decode_tps_ewma: float | None = None
        self._ttft_ewma_s: float | None = None
        # Cross-session toggle: log "measured ≈ X t/s" only once per threshold cross.
        self._logged_measurement_available: bool = False
        self._logged_pin_underuse: bool = False

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

    def _apply_cache_hints(self, body: dict[str, Any]) -> None:
        """llama-server host-memory prompt cache: reuses KV across calls with
        overlapping prefixes. Ollama ignores cache_prompt."""
        if self._inference_engine == "llamacpp":
            body["cache_prompt"] = True

    def _resolve_max_tokens(
        self,
        max_tokens: int | None,
        target_latency_s: float | None,
        min_tokens: int | None,
    ) -> int:
        """Pick the max_tokens cap per plans/gpu-scaling.md resolution order.

        1. Explicit max_tokens arg → use as-is.
        2. User-pinned per-server max_tokens → use pin.
        3. target_latency_scaling on, estimator ready →
           (target_latency_s - ttft_ewma) * decode_tps_ewma, floored by
           min_tokens and clamped by ctx_length//4 and MAX_TOKENS_HARD_CAP.
        4. Fall back to the static self._max_tokens default.
        """
        if max_tokens is not None:
            return max_tokens
        latency_ready = (
            self._target_latency_scaling
            and target_latency_s is not None
            and self._estimate_ready
        )
        if self._max_tokens_pinned:
            if latency_ready and not self._logged_pin_underuse:
                assert target_latency_s is not None
                suggested = self._derive_from_latency(target_latency_s, min_tokens)
                if suggested > self._max_tokens:
                    assert self._decode_tps_ewma is not None
                    assert self._ttft_ewma_s is not None
                    log.info(
                        "user-pinned max_tokens=%d leaves ~%d tokens on the "
                        "table vs measured (%.0f t/s decode, %.2fs ttft)",
                        self._max_tokens, suggested - self._max_tokens,
                        self._decode_tps_ewma, self._ttft_ewma_s,
                    )
                    self._logged_pin_underuse = True
            return self._max_tokens
        if latency_ready:
            assert target_latency_s is not None
            return self._derive_from_latency(target_latency_s, min_tokens)
        return self._max_tokens

    def _derive_from_latency(
        self, target_latency_s: float, min_tokens: int | None
    ) -> int:
        """Map (target_latency_s, ewmas) → int cap. Caller pre-checks EWMAs."""
        assert self._decode_tps_ewma is not None
        assert self._ttft_ewma_s is not None
        usable = target_latency_s - self._ttft_ewma_s
        floor = min_tokens if min_tokens is not None else 1
        if usable <= 0:
            return floor
        raw = int(usable * self._decode_tps_ewma)
        if self._context_length is not None:
            raw = min(raw, self._context_length // 4)
        raw = min(raw, self._MAX_TOKENS_HARD_CAP)
        return max(raw, floor)

    def _record_sample(
        self,
        completion_tokens: int,
        total_elapsed_s: float,
    ) -> None:
        """Feed a successful call into the decode/TTFT EWMAs.

        First sample: decode_tps ≈ completion/elapsed (no TTFT yet — biased
        low). Subsequent samples refine TTFT, then correct decode using it.
        """
        if completion_tokens <= 0 or total_elapsed_s <= 0:
            return
        if self._ttft_ewma_s is None:
            decode_tps = completion_tokens / total_elapsed_s
            ttft = 0.0
        else:
            elapsed_minus_ttft = total_elapsed_s - self._ttft_ewma_s
            if elapsed_minus_ttft <= 0:
                # Call finished faster than prior TTFT — skip, keeps estimator honest.
                return
            decode_tps = completion_tokens / elapsed_minus_ttft
            assert self._decode_tps_ewma is not None
            ttft = max(
                0.0, total_elapsed_s - completion_tokens / self._decode_tps_ewma
            )
        self._decode_tps_ewma = self._ewma_update(self._decode_tps_ewma, decode_tps)
        self._ttft_ewma_s = self._ewma_update(self._ttft_ewma_s, ttft)
        self._sample_count += 1
        if (
            not self._logged_measurement_available
            and self._sample_count >= self._MIN_SAMPLES_FOR_ESTIMATE
        ):
            log.info(
                "throughput measured: ≈%.0f t/s decode, %.2fs TTFT (%s @ %s)",
                self._decode_tps_ewma, self._ttft_ewma_s,
                self._model_name, self._api_url,
            )
            self._logged_measurement_available = True
        log.debug(
            "sample: tokens=%d elapsed=%.3fs decode≈%.1f t/s ttft≈%.2fs n=%d",
            completion_tokens, total_elapsed_s,
            self._decode_tps_ewma, self._ttft_ewma_s, self._sample_count,
        )

    def _ewma_update(self, prior: float | None, sample: float) -> float:
        """α-weighted exponential moving average. None prior → cold start."""
        if prior is None:
            return sample
        return (1 - self._EWMA_ALPHA) * prior + self._EWMA_ALPHA * sample

    def _clear_throughput_estimators(self) -> None:
        """Drop EWMA state on model/server change. Next 3 calls re-bootstrap."""
        self._sample_count = 0
        self._decode_tps_ewma = None
        self._ttft_ewma_s = None
        self._logged_measurement_available = False
        self._logged_pin_underuse = False

    @property
    def _estimate_ready(self) -> bool:
        """True when EWMAs have >=MIN_SAMPLES, usable for resolution rule 3."""
        return (
            self._sample_count >= self._MIN_SAMPLES_FOR_ESTIMATE
            and self._decode_tps_ewma is not None
            and self._ttft_ewma_s is not None
        )

    async def generate(
        self,
        prompt: str,
        max_tokens: int | None = None,
        *,
        enable_thinking: bool | None = None,
        response_format: dict[str, Any] | None = None,
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        effective_max = self._resolve_max_tokens(
            max_tokens, target_latency_s, min_tokens
        )
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": effective_max,
            "temperature": self._temperature,
        }
        self._apply_thinking_controls(body, enable_thinking)
        self._apply_cache_hints(body)
        if response_format is not None:
            body["response_format"] = response_format

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_s = time.monotonic() - start

        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        completion_tokens = usage.get("completion_tokens")
        if isinstance(completion_tokens, int):
            self._record_sample(completion_tokens, elapsed_s)

        return LLMResponse(
            text=text,
            tokens_used=tokens,
            model_name=self._model_name,
            latency_ms=elapsed_s * 1000,
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
        target_latency_s: float | None = None,
        min_tokens: int | None = None,
    ) -> LLMResponse:
        assert self._client is not None, "Call setup() first"

        start = time.monotonic()
        effective_max = self._resolve_max_tokens(
            max_tokens, target_latency_s, min_tokens
        )
        body: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": effective_max,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools
        self._apply_thinking_controls(body, enable_thinking)
        self._apply_cache_hints(body)
        if response_format is not None:
            body["response_format"] = response_format

        resp = await self._client.post(
            f"{self._api_url}/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed_s = time.monotonic() - start

        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        completion_tokens = usage.get("completion_tokens")
        if isinstance(completion_tokens, int):
            self._record_sample(completion_tokens, elapsed_s)

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
            latency_ms=elapsed_s * 1000,
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
        # Throughput estimator is also model-specific — a 32B model and a 4B
        # model on the same server have very different decode_tps.
        self._clear_throughput_estimators()
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
        self._clear_throughput_estimators()
        # Re-evaluate pin status against the new server's entry in config.
        key = canon_server_url(self._api_url)
        if key in self._per_server_max_tokens:
            self._max_tokens = int(self._per_server_max_tokens[key])
            self._max_tokens_pinned = True
        else:
            self._max_tokens_pinned = False
            self._max_tokens = self._initial_max_tokens
        log.info("API URL switched to: %s", self._api_url)

    @property
    def _native_root(self) -> str:
        """The non-OpenAI-compat root URL (strip trailing /v1) for native probes."""
        return self._api_url[:-3] if self._api_url.endswith("/v1") else self._api_url

    async def _probe_context_length(self) -> int | None:
        """Probe Ollama's native /api/show for the active model's context_length.

        Returns None on any error (non-Ollama backend, network failure, missing field).
        """
        assert self._client is not None
        try:
            resp = await self._client.post(
                f"{self._native_root}/api/show",
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

    async def _probe_llamacpp_props(self) -> int | None:
        """Probe llama-server's /props for default_generation_settings.n_ctx.

        Counterpart to _probe_context_length for the llamacpp backend —
        llama-server has no /api/show but exposes n_ctx on /props.
        """
        assert self._client is not None
        try:
            resp = await self._client.get(
                f"{self._native_root}/props",
                timeout=self._PROBE_TIMEOUT_S,
            )
            resp.raise_for_status()
            gen = resp.json().get("default_generation_settings") or {}
            n_ctx = gen.get("n_ctx")
            if not isinstance(n_ctx, (int, float)) or n_ctx <= 0:
                return None
            return int(n_ctx)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.debug("llama-server /props probe failed: %s", e)
            return None

    async def _apply_auto_max_tokens(self) -> None:
        """Probe server capability and update max_tokens when not user-pinned."""
        if self._inference_engine == "llamacpp":
            ctx = await self._probe_llamacpp_props()
        else:
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
