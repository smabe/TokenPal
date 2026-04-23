"""Follow-up state for cloud /research calls.

One `FollowupSession` lives on the Brain at a time. A successful cloud-backed
`/research` (synth / search / deep) stashes its state here; a subsequent
`research_followup` tool call or `/followup` slash reads it back and re-engages
Anthropic with the cached prefix.

See plans/shipped/smarter-buddy.md for the why. Short version:
- Option B handles simple follow-ups for free via the conversation LLM's rolling
  context (no tool call).
- Option A — this module — handles escalated follow-ups that need new info, by
  replaying the message history with a ``cache_control`` breakpoint so cached
  tokens bill at 10% instead of full price.

No on-disk persistence — follow-up state is process-scoped. Overwritten every
time a new cloud /research completes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from tokenpal.brain.research import Source

FollowupMode = Literal["synth", "search", "deep"]


@dataclass
class FollowupSession:
    """State carried from a cloud /research to its follow-ups.

    ``messages`` is the full Anthropic conversation history. Synth-mode
    sessions stash it as ``[{user: original_prompt}, {assistant: answer_text}]``;
    search/deep sessions stash the ``run_deep`` pause-turn loop output verbatim.
    Grows by two turns (user + assistant) per followup; bounded by ``max_followups``.

    ``tools`` is the exact list sent to Anthropic on the initial call
    (empty for synth). Re-sent unchanged on follow-ups so schema pinning is
    automatic.
    """

    mode: FollowupMode
    model: str
    sources: list[Source]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    ttl_s: int
    max_followups: int
    followup_count: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)


def is_expired(session: FollowupSession, now: float | None = None) -> bool:
    """TTL check. Sliding-window is deliberately NOT used — cost bound stays hard."""
    at = now if now is not None else time.time()
    return (at - session.last_used_at) > session.ttl_s


def over_cap(session: FollowupSession) -> bool:
    return session.followup_count >= session.max_followups


def touch(session: FollowupSession) -> None:
    """Mark session used NOW without incrementing followup_count.

    Call from telemetry / status paths. For actual follow-up fires, call
    ``bump`` which also increments the counter.
    """
    session.last_used_at = time.time()


def bump(session: FollowupSession) -> None:
    session.followup_count += 1
    session.last_used_at = time.time()
