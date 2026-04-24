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
from dataclasses import dataclass, field
from typing import Any, NamedTuple

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

    ``messages`` and ``tools`` are captured for follow-up persistence — the
    orchestrator snapshots them into a ``FollowupSession`` so a subsequent
    `research_followup` tool call can replay the Anthropic conversation with a
    cache_control breakpoint. Empty for synth mode (which uses the one-shot
    ``synthesize`` path).
    """

    text: str
    tokens_used: int
    iterations: int
    latency_ms: float
    finish_reason: str | None
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FollowupResult:
    """Raw output of a cloud follow-up call.

    ``messages`` is the full updated conversation (prior history + new user
    turn + new assistant turn(s)) — caller replaces the session's ``messages``
    with this so the next follow-up picks up where this one left off.

    ``cache_read_tokens`` / ``cache_creation_tokens`` come from the Anthropic
    ``usage`` object. A ``cache_read_tokens`` of ~0 on a followup means the
    cache_control breakpoint didn't hit — worth a telemetry warning.
    """

    text: str
    messages: list[dict[str, Any]]
    tokens_used: int
    cache_read_tokens: int
    cache_creation_tokens: int
    iterations: int
    latency_ms: float
    finish_reason: str | None


class _LoopResult(NamedTuple):
    final_text: str
    messages: list[dict[str, Any]]
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    iterations: int
    stop_reason: str | None
    latency_ms: float


def _to_cloud_error(
    exc: Exception, *, timeout_s: float, label: str,
) -> CloudBackendError:
    """Translate an Anthropic SDK exception into our CloudBackendError.

    ``label`` gets embedded into the error message so callers can tell whether
    a 401 hit on the initial research, a deep-mode loop, or a follow-up. The
    ``kind`` attribute is the stable programmatic identifier — `/cloud status`
    and the runner's fallback path key off it.
    """
    from anthropic import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        PermissionDeniedError,
        RateLimitError,
    )
    if isinstance(exc, AuthenticationError):
        return CloudBackendError(
            "Anthropic rejected the API key (401). Run /cloud enable "
            "with a valid key.",
            kind="auth",
        )
    if isinstance(exc, PermissionDeniedError):
        txt = str(exc).lower()
        if "credit balance" in txt or "insufficient" in txt:
            return CloudBackendError(
                "Workspace has no credit. Add funds at console.anthropic.com.",
                kind="no_credit",
            )
        return CloudBackendError(
            f"Anthropic denied the request (403): {exc}", kind="permission",
        )
    if isinstance(exc, RateLimitError):
        retry_after = None
        try:
            retry_after = (
                float(exc.response.headers.get("retry-after", "") or 0) or None
            )
        except (AttributeError, ValueError):
            pass
        return CloudBackendError(
            f"Anthropic rate limit hit (429) on {label}.",
            kind="rate_limit",
            retry_after=retry_after,
        )
    if isinstance(exc, APITimeoutError):
        return CloudBackendError(
            f"{label} timed out after {timeout_s}s.", kind="timeout",
        )
    if isinstance(exc, APIConnectionError):
        return CloudBackendError(
            f"Could not reach Anthropic: {exc}", kind="network",
        )
    if isinstance(exc, BadRequestError):
        return CloudBackendError(
            f"Anthropic rejected the {label} request (400): {exc}",
            kind="bad_request",
        )
    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", "?")
        return CloudBackendError(
            f"Anthropic returned {status_code}: {exc}", kind="api_status",
        )
    return CloudBackendError(str(exc), kind="unknown")


def _run_messages_loop(
    *,
    client: Any,
    messages: list[dict[str, Any]],
    base_kwargs: dict[str, Any],
    timeout_s: float,
    label: str,
    max_continuations: int,
) -> _LoopResult:
    """Shared pause-turn loop for research_deep + followup.

    Mutates ``messages`` in place (appends assistant turns). Always terminates
    either on a non-pause_turn ``stop_reason`` or by hitting ``max_continuations``.
    """
    from anthropic import APIError

    total_output = 0
    cache_read = 0
    cache_creation = 0
    iterations = 0
    last_stop_reason: str | None = None
    start = time.monotonic()

    while True:
        try:
            msg = client.messages.create(
                messages=messages,
                **base_kwargs,
            )
        except APIError as e:
            raise _to_cloud_error(e, timeout_s=timeout_s, label=label) from e

        usage = getattr(msg, "usage", None)
        if usage is not None:
            total_output += int(getattr(usage, "output_tokens", 0) or 0)
            cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            cache_creation += int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )

        last_stop_reason = getattr(msg, "stop_reason", None)
        assistant_content = _content_to_serializable(
            getattr(msg, "content", []) or []
        )
        messages.append({"role": "assistant", "content": assistant_content})

        if last_stop_reason != "pause_turn":
            break
        if iterations >= max_continuations:
            log.warning(
                "%s hit max continuations (%d); stopping",
                label, max_continuations,
            )
            break
        iterations += 1

    latency_ms = (time.monotonic() - start) * 1000.0
    final_text = _extract_text_from_blocks(messages[-1]["content"]).strip()
    return _LoopResult(
        final_text=final_text,
        messages=messages,
        output_tokens=total_output,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        iterations=iterations,
        stop_reason=last_stop_reason,
        latency_ms=latency_ms,
    )


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
        from anthropic import APIError

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
            # Anthropic enforces the schema server-side but requires
            # additionalProperties: false on every object; the local schema
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
        except APIError as e:
            raise _to_cloud_error(
                e, timeout_s=self.timeout_s, label="research",
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

        client = anthropic.Anthropic(
            api_key=self.api_key, timeout=self.deep_timeout_s,
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
        result = _run_messages_loop(
            client=client,
            messages=messages,
            base_kwargs=base_kwargs,
            timeout_s=self.deep_timeout_s,
            label="deep-mode",
            max_continuations=_MAX_DEEP_CONTINUATIONS,
        )

        return CloudBackendDeepResult(
            text=result.final_text,
            tokens_used=result.output_tokens,
            iterations=result.iterations,
            latency_ms=result.latency_ms,
            finish_reason=_map_stop_reason(result.stop_reason),
            messages=result.messages,
            tools=list(tools),
        )

    def followup(
        self,
        prior_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        new_user_turn: str,
        *,
        max_tokens: int = 3000,
        enable_cache: bool = True,
    ) -> FollowupResult:
        """Ask a follow-up against a cached cloud /research exchange.

        ``prior_messages`` is the session's full history (see
        ``FollowupSession.messages``). We append the new user turn, add a
        ``cache_control: ephemeral`` breakpoint on the last assistant block so
        the prior exchange gets cached at 10% billing, and invoke the same
        pause-turn loop ``research_deep`` uses so follow-ups that trigger
        server-side tool calls resume automatically.

        ``tools`` is the exact list the original call used (empty for synth
        mode). Re-sent unchanged — schema pinning is automatic.
        """
        import anthropic

        timeout = self.deep_timeout_s if tools else self.timeout_s
        client = anthropic.Anthropic(api_key=self.api_key, timeout=timeout)

        base = (
            _apply_cache_breakpoint(prior_messages)
            if enable_cache else list(prior_messages)
        )
        messages: list[dict[str, Any]] = [
            *base,
            {"role": "user", "content": new_user_turn},
        ]

        base_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
        }
        if tools:
            base_kwargs["tools"] = tools
        if self.model in _THINKING_MODELS:
            base_kwargs["thinking"] = {"type": "adaptive"}

        result = _run_messages_loop(
            client=client,
            messages=messages,
            base_kwargs=base_kwargs,
            timeout_s=timeout,
            label="follow-up",
            max_continuations=_MAX_DEEP_CONTINUATIONS,
        )

        return FollowupResult(
            text=result.final_text,
            messages=result.messages,
            tokens_used=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
            iterations=result.iterations,
            latency_ms=result.latency_ms,
            finish_reason=_map_stop_reason(result.stop_reason),
        )


def _apply_cache_breakpoint(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a shallow copy of ``messages`` with exactly ONE
    ``cache_control: ephemeral`` on the last content block of the last
    assistant turn. Strips any pre-existing cache_control markers first.

    Why strip: session.messages accumulates the exact payload we sent on the
    prior follow-up — including its cache_control. If we only ADD a new
    breakpoint, every follow-up adds one more, hitting Anthropic's hard
    limit of 4 cache_control blocks per request at followup #5 and 400'ing.
    The right behavior is: only the LAST assistant turn carries the
    breakpoint; previous turns are cached via prefix-matching against
    earlier requests' cache writes, not by carrying the marker forward.

    Anthropic caches everything UP TO AND INCLUDING the cache_control block,
    so placing it on the tail of the most recent assistant turn maximizes
    the cached prefix.

    Synth-mode content is stashed as a plain string — wrap it into a single
    text block so cache_control can attach. Deep/search-mode content is
    already a list of SDK blocks (text / tool_use / tool_result); mutate a
    shallow copy of the last block to carry cache_control.
    """
    if not messages:
        return list(messages)
    out = [_strip_cache_control(m) for m in messages]
    for idx in range(len(out) - 1, -1, -1):
        if out[idx].get("role") != "assistant":
            continue
        content = out[idx]["content"]
        if isinstance(content, str):
            out[idx]["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
            break
        if isinstance(content, list) and content:
            new_content = list(content)
            last = new_content[-1]
            if isinstance(last, dict):
                last_dict = dict(last)
            elif hasattr(last, "model_dump"):
                last_dict = last.model_dump()
            else:
                last_dict = dict(last)  # best effort
            last_dict["cache_control"] = {"type": "ephemeral"}
            new_content[-1] = last_dict
            out[idx]["content"] = new_content
            break
    return out


def _strip_cache_control(message: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``message`` with any ``cache_control`` fields
    removed from its content blocks. String content passes through unchanged.
    SDK pydantic blocks are returned as dicts via ``model_dump()``.
    """
    out = dict(message)
    content = out.get("content")
    if not isinstance(content, list):
        return out
    new_content: list[Any] = []
    for block in content:
        if isinstance(block, dict):
            if "cache_control" in block:
                block = {k: v for k, v in block.items() if k != "cache_control"}
            new_content.append(block)
            continue
        if hasattr(block, "model_dump"):
            dumped = block.model_dump()
            if "cache_control" in dumped:
                dumped = {
                    k: v for k, v in dumped.items() if k != "cache_control"
                }
            new_content.append(dumped)
            continue
        new_content.append(block)
    out["content"] = new_content
    return out


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
