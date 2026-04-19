"""Anthropic-backed cloud inference for /research synth.

Thin wrapper around the official ``anthropic`` SDK. Only used by the
research pipeline's synth stage when /cloud enable has been run and
[cloud_llm] enabled = true in config. Never instantiated for observations,
conversation, planner, or idle-tool rolls.

Not an ``AbstractLLMBackend`` subclass by design: we don't want this to
be discoverable by the generic backend registry. Its one exposed method
(``synthesize``) mirrors the subset of ``AbstractLLMBackend.generate``
that the research synth actually calls.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from tokenpal.llm.base import LLMResponse

log = logging.getLogger(__name__)

# Allowlist — prevents typos / random model IDs from landing in config.toml
# via /cloud model <id>. Extend when Anthropic ships a new tier we want to
# support.
ALLOWED_MODELS: tuple[str, ...] = (
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)


class CloudBackendError(Exception):
    """Raised for any failure calling the cloud backend.

    The research runner catches this and falls back to local synth. The
    ``kind`` attribute lets callers (like /cloud status) distinguish
    actionable errors (auth, credit) from transient ones (network, rate).
    """

    def __init__(
        self, message: str, kind: str = "unknown", retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class CloudBackend:
    """Anthropic API wrapper scoped to /research synth."""

    api_key: str
    model: str = "claude-haiku-4-5"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.model not in ALLOWED_MODELS:
            raise ValueError(
                f"cloud model {self.model!r} not in allowlist {ALLOWED_MODELS}"
            )
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise CloudBackendError(
                "anthropic SDK not installed. Run: pip install anthropic",
                kind="missing_sdk",
            ) from e

    def synthesize(
        self,
        prompt: str,
        *,
        max_tokens: int = 1800,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """One-shot generation for the /research synth stage.

        Returns an ``LLMResponse`` with the same shape ``HttpBackend.generate``
        uses, so ``ResearchRunner._synthesize`` can pass the result straight
        into ``_parse_synth_json`` and the downstream validation pipeline.
        """
        import anthropic
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            PermissionDeniedError,
            RateLimitError,
        )

        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout_s)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_schema is not None:
            # Constrain output to valid JSON matching the synth schema. This
            # replaces the fragile ``response_format`` advisory we use for
            # Ollama; Anthropic enforces the schema server-side, but requires
            # additionalProperties: false on every object - the local schema
            # omits it because Ollama/llama-server don't require it.
            kwargs["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": _harden_schema_for_anthropic(json_schema),
                },
            }

        start = time.monotonic()
        try:
            msg = client.messages.create(**kwargs)
        except AuthenticationError as e:
            raise CloudBackendError(
                "Anthropic rejected the API key (401). Run /cloud enable "
                "with a valid key.",
                kind="auth",
            ) from e
        except PermissionDeniedError as e:
            # 403. Most common cause is an unfunded workspace — detect that
            # specifically so /cloud status can nudge the user to add credit.
            msg_txt = str(e).lower()
            if "credit balance" in msg_txt or "insufficient" in msg_txt:
                raise CloudBackendError(
                    "Workspace has no credit. Add funds at console.anthropic.com.",
                    kind="no_credit",
                ) from e
            raise CloudBackendError(
                f"Anthropic denied the request (403): {e}", kind="permission",
            ) from e
        except RateLimitError as e:
            retry_after = None
            try:
                retry_after = float(e.response.headers.get("retry-after", "") or 0) or None
            except (AttributeError, ValueError):
                pass
            raise CloudBackendError(
                "Anthropic rate limit hit (429).", kind="rate_limit",
                retry_after=retry_after,
            ) from e
        except APITimeoutError as e:
            raise CloudBackendError(
                f"Anthropic request timed out after {self.timeout_s}s.",
                kind="timeout",
            ) from e
        except APIConnectionError as e:
            raise CloudBackendError(
                f"Could not reach Anthropic: {e}", kind="network",
            ) from e
        except BadRequestError as e:
            raise CloudBackendError(
                f"Anthropic rejected the request (400): {e}", kind="bad_request",
            ) from e
        except APIStatusError as e:
            raise CloudBackendError(
                f"Anthropic returned {e.status_code}: {e}", kind="api_status",
            ) from e

        latency_ms = (time.monotonic() - start) * 1000.0
        text = _extract_text(msg)
        usage = getattr(msg, "usage", None)
        tokens_used = 0
        if usage is not None:
            tokens_used = int(getattr(usage, "output_tokens", 0) or 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            model_name=self.model,
            latency_ms=latency_ms,
            finish_reason=_map_stop_reason(getattr(msg, "stop_reason", None)),
        )


def _harden_schema_for_anthropic(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively add additionalProperties: false to every object schema.

    Anthropic's output_config.format rejects object schemas that don't set
    this explicitly. Our local SYNTH_SCHEMA omits it because Ollama and
    llama-server both ignore the field. We harden a copy at send-time so
    the local schema stays untouched.
    """
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {
                k: _harden_schema_for_anthropic(v) for k, v in props.items()
            }
    items = out.get("items")
    if isinstance(items, dict):
        out["items"] = _harden_schema_for_anthropic(items)
    elif isinstance(items, list):
        out["items"] = [_harden_schema_for_anthropic(i) for i in items]
    for combinator in ("anyOf", "allOf", "oneOf"):
        vals = out.get(combinator)
        if isinstance(vals, list):
            out[combinator] = [_harden_schema_for_anthropic(v) for v in vals]
    return out


def _extract_text(msg: Any) -> str:
    """Pull plain text from an Anthropic Message's content blocks."""
    parts: list[str] = []
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def _map_stop_reason(reason: str | None) -> str | None:
    """Translate Anthropic stop reasons onto the OpenAI-shaped finish_reason
    strings the research runner already handles (``stop`` / ``length``)."""
    if reason is None:
        return None
    if reason == "max_tokens":
        return "length"
    if reason in ("end_turn", "stop_sequence"):
        return "stop"
    return reason
