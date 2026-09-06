"""Base class for LLM-callable actions (tools)."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class RateLimit:
    """Cap how often a tool can fire inside a rolling window.

    Enforced by ``ToolInvoker``; exceeded calls fail-fast with a
    ``ActionResult(success=False)`` rather than sleeping or queueing.
    """

    max_calls: int
    window_s: float


@dataclass
class ActionResult:
    """Result from executing an action."""

    output: str
    success: bool = True
    display_url: str | None = None
    # Multiple clickable links to surface in the chat log, each a
    # (label, url) pair. Used by multi-source tools like `research`.
    display_urls: list[tuple[str, str]] | None = None
    # Verbatim text to surface in the chat log alongside the buddy's reaction.
    # Used by content-delivery tools where the value to the user IS the raw
    # tool output, not just the LLM's riff on it. Sender is responsible for
    # scrubbing third-party text via ``scrub_body`` before assigning here.
    display_text: str | None = None


def consent_error(category_label: str) -> ActionResult:
    """Uniform refusal when the user hasn't granted *category_label*."""
    return ActionResult(
        output=f"Tool requires '{category_label}' consent. Open /consent to grant it.",
        success=False,
    )


class AbstractAction(abc.ABC):
    """Base class every LLM-callable action must inherit from.

    Subclasses declare metadata as class variables for discovery:
        action_name: identifier used in tool definitions sent to the LLM
        description: one-line description the LLM sees to decide when to call it
        parameters: JSON Schema dict describing accepted arguments
        platforms: tuple of supported platforms ("windows", "darwin", "linux")
    """

    action_name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict[str, Any]]
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    # `safe` = no side effects beyond reading state (read by `--check` only).
    # `requires_confirm` is load-bearing: the /agent loop and the plain-chat
    # tool executor both raise a modal before running the action, and the
    # ambient observation path drops it from the offered tools entirely.
    # Setting it True on a new action therefore changes what a user sees.
    safe: ClassVar[bool] = False
    requires_confirm: ClassVar[bool] = True
    # Opt-in throttle. When set, the registry's invoker fails calls that
    # would exceed ``max_calls`` inside the trailing ``window_s`` seconds.
    rate_limit: ClassVar[RateLimit | None] = None
    # When False, the agent in-run result cache skips this tool (e.g. because
    # the output is time-sensitive or carries side effects worth re-running).
    cacheable: ClassVar[bool] = True
    # Output is text read from another desktop app. The agent runner redacts
    # the trace, never caches the result, and drops network tools for the rest
    # of the run; the conversation path never exposes this tool at all.
    reads_desktop_content: ClassVar[bool] = False
    # True only when the tool is suitable for an unattended ambient tick.
    # Defaults False so a new tool is not ADVERTISED there until a human opts
    # it in. Advertise-only: `Brain._execute_tool_call` resolves any name the
    # model emits against the full enabled set, so this narrows what the model
    # is shown, not what it can run. Independent of `requires_confirm`, which
    # is about raising a modal.
    allow_unprompted: ClassVar[bool] = False
    # True when the tool writes model-authored text to a durable local sink
    # whose rows survive `_prune` and `/clear`. The agent runner drops such a
    # tool from the advertised specs and refuses it at execution once desktop
    # content is in the run's context.
    writes_durable_sink: ClassVar[bool] = False
    # Argument names carrying a filesystem path. Empty means the invoker does
    # no path work for this tool at all — the gate that keeps a git subprocess
    # off every non-path tool call. A declared name is resolved and contained
    # before `execute` runs, and arrives as a `ResolvedPath` instead of a str.
    path_params: ClassVar[tuple[str, ...]] = ()
    # Which roots a declared path must resolve inside: the cwd's git worktree,
    # or the folders in `[paths] allowed_dirs`. Also fixes where a relative
    # path is anchored — at the repo root, or at the process cwd.
    path_roots: ClassVar[Literal["git_root", "allowed_dirs"]] = "git_root"
    # Strength of the screen applied to the RAW argument, before resolution.
    # "broad" adds REJECT_PATH and contains_sensitive_term; "narrow" screens the
    # raw name not at all, because on an absolute path those two refuse benign
    # files under a badly-named folder. The RESOLVED name is always screened
    # with path_is_sensitive regardless. Defaults to the stricter value so a
    # tool that declares a path and forgets the screen still gets one.
    path_screen: ClassVar[Literal["broad", "narrow"]] = "broad"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @abc.abstractmethod
    async def execute(self, **kwargs: Any) -> ActionResult:
        """Run the action with the given arguments. Must be safe and bounded."""

    async def teardown(self) -> None:
        """Release resources. Override if the action holds async state."""

    def to_tool_spec(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible tool definition for the LLM."""
        return {
            "type": "function",
            "function": {
                "name": self.action_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
