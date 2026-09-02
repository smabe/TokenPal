"""Scrubbing for text this process did not author.

Two predicates, matching the two lists in ``tokenpal.brain.personality``:
``scrub_body`` filters on the full sensitive-app list, ``scrub_content_body``
on the narrower identity-critical list meant for untrusted prose.
"""

from __future__ import annotations

from collections.abc import Callable

from tokenpal.brain.personality import (
    contains_sensitive_content_term,
    contains_sensitive_term,
)

_SENSITIVE_PLACEHOLDER = "[filtered]"


def _scrub(body: str, is_sensitive: Callable[[str | None], bool]) -> str:
    safe_lines = [
        _SENSITIVE_PLACEHOLDER if is_sensitive(line) else line
        for line in body.splitlines() or [body]
    ]
    return "\n".join(safe_lines)


def scrub_body(body: str) -> str:
    """Line-wise sensitive-app-name scrub. One bad token shouldn't nuke the
    whole response, so we filter per-line. Guards two sinks: the network
    tools' ``<tool_result>`` envelope, and ``ActionResult.display_text``,
    which is persisted to the chat log."""
    return _scrub(body, contains_sensitive_term)


def scrub_content_body(body: str) -> str:
    """Line-wise scrub for long-form untrusted prose. Uses the narrower
    identity-critical list, because the full app list matches ordinary
    English ("signal", "health", "chase") and would blank real content --
    see the comment above ``SENSITIVE_CONTENT_TERMS``."""
    return _scrub(body, contains_sensitive_content_term)
