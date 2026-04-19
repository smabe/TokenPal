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

# Models that support the dynamic-filtering web_search_20260209 /
# web_fetch_20260209 tools used by /research deep mode. Haiku 4.5 falls
# back to the older web_search_20250305 (full-results-into-context,
# token cost explodes) so we gate deep mode off it entirely.
DEEP_MODE_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6",
    "claude-opus-4-7",
})

# Hard cap on pause_turn continuations. Server-side tool loops hit a
# default ~10-iteration stop and return stop_reason="pause_turn"; we
# re-send the conversation to continue. Each continuation re-bills the
# FULL accumulated context - a single retry can easily double a $1 call.
# One continuation is a compromise: enough headroom for Sonnet to
# finalize after a well-scoped search, no room for tangents.
_MAX_DEEP_CONTINUATIONS = 1

# Per-tool invocation caps. Anthropic's web_search / web_fetch tools
# accept ``max_uses`` which the server enforces mid-loop. Without these,
# Sonnet will happily search 8-10 things and fetch them all. Three
# searches + five fetches gives good coverage without the runaway cost.
_DEEP_MAX_SEARCHES = 3
_DEEP_MAX_FETCHES = 5

# Allowlist — prevents typos / random model IDs from landing in config.toml
# via /cloud model <id>. Extend when Anthropic ships a new tier we want to
# support.
ALLOWED_MODELS: tuple[str, ...] = (
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)

# Models that support adaptive thinking. Haiku 4.5 errors if sent thinking
# params; Sonnet 4.6 and Opus 4.7 both support it and benefit from it on
# synthesis tasks (deeper reasoning, better pick justifications, more
# nuanced verdicts). See shared/models.md in the claude-api skill.
_THINKING_MODELS: frozenset[str] = frozenset({
    "claude-sonnet-4-6",
    "claude-opus-4-7",
})


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
class CloudBackendDeepResult:
    """Raw output of a /research deep-mode call.

    ``text`` is the model's final content (should be JSON matching the
    extended synth schema with a ``sources`` array); ``tokens_used`` is the
    sum of output_tokens across all continuations. ``iterations`` counts how
    many pause_turn continuations were needed — 0 means the first response
    was final.
    """

    text: str
    tokens_used: int
    iterations: int
    latency_ms: float
    finish_reason: str | None


@dataclass
class CloudBackend:
    """Anthropic API wrapper scoped to /research synth."""

    api_key: str
    model: str = "claude-haiku-4-5"
    timeout_s: float = 30.0
    # Deep mode does full server-side search + fetch + reasoning, which
    # routinely takes 1-3 minutes per API call (and each pause_turn
    # continuation pays the same cost). Using ``timeout_s`` would have us
    # timing out on the first call every time. 5 min per call is enough
    # headroom for the "Sonnet went off on a tangent" case without
    # hanging indefinitely.
    deep_timeout_s: float = 300.0

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
        if self.model in _THINKING_MODELS:
            # Adaptive thinking lets the model allocate reasoning budget
            # dynamically - better synth quality on nuanced questions.
            # Haiku 4.5 errors if sent thinking params, so only on Sonnet/Opus.
            kwargs["thinking"] = {"type": "adaptive"}
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

    def research_deep(
        self,
        prompt: str,
        *,
        max_tokens: int = 6000,
        json_schema: dict[str, Any] | None = None,
        include_fetch: bool = True,
    ) -> CloudBackendDeepResult:
        """Run /research in deep mode using Anthropic's server-side web search.

        Attaches ``web_search_20260209`` + ``web_fetch_20260209`` to a single
        ``messages.create`` call and lets the server orchestrate the
        search->fetch->synthesize loop. When the loop hits its default
        iteration cap the API returns ``stop_reason="pause_turn"``; we
        re-send the full conversation (original user message + the assistant
        turn so far) and the API resumes automatically. Do NOT add a
        "please continue" user message — the API detects the trailing
        server_tool_use block on the assistant side and picks up from there.

        Gated to Sonnet 4.6+ at the config layer; sent anyway as a defense
        in depth so a forced-config-edit YOLO surfaces as a BadRequestError
        rather than a silently-degraded result.
        """
        if self.model not in DEEP_MODE_MODELS:
            raise CloudBackendError(
                f"model {self.model!r} does not support deep mode "
                f"(requires one of {sorted(DEEP_MODE_MODELS)})",
                kind="bad_model",
            )

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

        client = anthropic.Anthropic(
            api_key=self.api_key, timeout=self.deep_timeout_s
        )
        tools: list[dict[str, Any]] = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": _DEEP_MAX_SEARCHES,
            },
        ]
        if include_fetch:
            tools.append({
                "type": "web_fetch_20260209",
                "name": "web_fetch",
                "max_uses": _DEEP_MAX_FETCHES,
            })
        base_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "tools": tools,
            "thinking": {"type": "adaptive"},
        }
        if json_schema is not None:
            base_kwargs["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": _harden_schema_for_anthropic(json_schema),
                },
            }

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        total_output_tokens = 0
        last_stop_reason: str | None = None
        iterations = 0
        start = time.monotonic()

        while True:
            try:
                msg = client.messages.create(
                    messages=messages,  # type: ignore[arg-type]
                    **base_kwargs,
                )
            except AuthenticationError as e:
                raise CloudBackendError(
                    "Anthropic rejected the API key (401).", kind="auth",
                ) from e
            except PermissionDeniedError as e:
                txt = str(e).lower()
                if "credit balance" in txt or "insufficient" in txt:
                    raise CloudBackendError(
                        "Workspace has no credit. Add funds at "
                        "console.anthropic.com.",
                        kind="no_credit",
                    ) from e
                raise CloudBackendError(
                    f"Anthropic denied the request (403): {e}", kind="permission",
                ) from e
            except RateLimitError as e:
                retry_after = None
                try:
                    retry_after = (
                        float(e.response.headers.get("retry-after", "") or 0)
                        or None
                    )
                except (AttributeError, ValueError):
                    pass
                raise CloudBackendError(
                    "Anthropic rate limit hit (429) on web search tools.",
                    kind="rate_limit",
                    retry_after=retry_after,
                ) from e
            except APITimeoutError as e:
                raise CloudBackendError(
                    f"Deep-mode request timed out after {self.deep_timeout_s}s.",
                    kind="timeout",
                ) from e
            except APIConnectionError as e:
                raise CloudBackendError(
                    f"Could not reach Anthropic: {e}", kind="network",
                ) from e
            except BadRequestError as e:
                raise CloudBackendError(
                    f"Anthropic rejected the deep-mode request (400): {e}",
                    kind="bad_request",
                ) from e
            except APIStatusError as e:
                raise CloudBackendError(
                    f"Anthropic returned {e.status_code}: {e}",
                    kind="api_status",
                ) from e

            usage = getattr(msg, "usage", None)
            if usage is not None:
                total_output_tokens += int(
                    getattr(usage, "output_tokens", 0) or 0
                )

            last_stop_reason = getattr(msg, "stop_reason", None)
            if last_stop_reason != "pause_turn":
                # end_turn / stop_sequence / max_tokens / tool_use (shouldn't
                # happen for server tools) — we're done looping.
                assistant_content = _content_to_serializable(
                    getattr(msg, "content", []) or []
                )
                messages.append({"role": "assistant", "content": assistant_content})
                break

            if iterations >= _MAX_DEEP_CONTINUATIONS:
                log.warning(
                    "deep-mode hit max continuations (%d); stopping with "
                    "whatever the model has produced",
                    _MAX_DEEP_CONTINUATIONS,
                )
                assistant_content = _content_to_serializable(
                    getattr(msg, "content", []) or []
                )
                messages.append({"role": "assistant", "content": assistant_content})
                break

            iterations += 1
            assistant_content = _content_to_serializable(
                getattr(msg, "content", []) or []
            )
            messages.append({"role": "assistant", "content": assistant_content})
            # No user follow-up; the API detects the trailing
            # server_tool_use block and resumes automatically.

        latency_ms = (time.monotonic() - start) * 1000.0
        # Extract text from the LAST assistant message we appended.
        last_assistant = messages[-1]
        final_text = _extract_text_from_blocks(last_assistant["content"]).strip()

        return CloudBackendDeepResult(
            text=final_text,
            tokens_used=total_output_tokens,
            iterations=iterations,
            latency_ms=latency_ms,
            finish_reason=_map_stop_reason(last_stop_reason),
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


def _extract_text_from_blocks(blocks: list[Any]) -> str:
    """Pull plain text from already-serialized content blocks (dicts or SDK objects)."""
    parts: list[str] = []
    for block in blocks or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", "") or "")
        elif getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _content_to_serializable(blocks: list[Any]) -> list[Any]:
    """Pass SDK content blocks back to the API on a pause_turn continuation.

    The SDK accepts its own pydantic block objects verbatim when echoed into
    ``messages=[...]``, so we just return the list as-is. Wrapped for clarity
    and to give tests a seam to substitute dict-shaped blocks.
    """
    return list(blocks)


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
