"""Natural-language triggers for slash commands.

Lets the user say "give me a summary" or "remind me to finish the auth PR"
instead of typing `/summary` or `/intent finish the auth PR`.

`match_nl_command(text)` returns `(command_name, args)` or None. The caller
dispatches the hit through the normal slash-command pipeline, so behavior
(bubbles, errors, chat logging) stays identical.
"""

from __future__ import annotations

import re

# Bare timer phrases like "remind me in 5 min to drink water" should fall
# through to the LLM (which owns the timer tool), not become an intent.
_TIMER_HINT = re.compile(
    r"\bin\s+\d+\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b",
    re.IGNORECASE,
)

_SUMMARY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "summarize today", "recap yesterday"
    (re.compile(r"^(?:summari[sz]e|recap)\s+(today|yesterday)$", re.IGNORECASE), r"\1"),
    # "today's summary", "yesterdays recap"
    (re.compile(r"^(today|yesterday)'?s?\s+(?:summary|recap)$", re.IGNORECASE), r"\1"),
    # "what did I do today/yesterday"
    (
        re.compile(
            r"^what\s+did\s+i\s+(?:do|work\s+on|accomplish|get\s+done)\s+(today|yesterday)$",
            re.IGNORECASE,
        ),
        r"\1",
    ),
    # Bare forms default to the /summary default (yesterday).
    (
        re.compile(
            r"^(?:(?:give\s+me|gimme|show\s+me)\s+(?:a|the)?\s*)?"
            r"(?:daily\s+|end[-\s]?of[-\s]?day\s+)?"
            r"(?:summari[sz]e|summary|recap)$",
            re.IGNORECASE,
        ),
        "",
    ),
    (re.compile(r"^what\s+did\s+i\s+(?:do|get\s+done|accomplish)$", re.IGNORECASE), ""),
]

_INTENT_STATUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^what(?:'?s|\s+is)\s+my\s+(?:goal|intent|focus)$", re.IGNORECASE),
    re.compile(r"^what\s+am\s+i\s+working\s+on$", re.IGNORECASE),
    re.compile(r"^(?:show|check)\s+(?:my\s+)?(?:goal|intent|focus)$", re.IGNORECASE),
]

_INTENT_CLEAR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:clear|forget|cancel|reset|drop|delete)\s+(?:my\s+)?(?:goal|intent|focus)$",
        re.IGNORECASE,
    ),
]

_INTENT_SET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^remind\s+me\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^remind\s+me\s+(?!to\b)(.+)$", re.IGNORECASE),
    re.compile(r"^(?:set\s+)?my\s+(?:goal|intent|focus)\s+(?:is|to)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^set\s+(?:my\s+)?(?:goal|intent|focus)\s+(?:to\s+)?(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i'?m|i\s+am)\s+(?:currently\s+)?working\s+on\s+(.+)$", re.IGNORECASE),
]


def match_nl_command(text: str) -> tuple[str, str] | None:
    """Return (command_name, args) for a natural-language hit, else None."""
    stripped = text.strip().rstrip("?.!").strip()
    if not stripped or stripped.startswith("/"):
        return None

    for pattern, arg_template in _SUMMARY_PATTERNS:
        m = pattern.match(stripped)
        if m is not None:
            args = m.expand(arg_template).lower() if arg_template else ""
            return ("summary", args)

    for pattern in _INTENT_STATUS_PATTERNS:
        if pattern.match(stripped):
            return ("intent", "status")

    for pattern in _INTENT_CLEAR_PATTERNS:
        if pattern.match(stripped):
            return ("intent", "clear")

    for pattern in _INTENT_SET_PATTERNS:
        m = pattern.match(stripped)
        if m is not None:
            if _TIMER_HINT.search(stripped):
                return None
            goal = m.group(1).strip().rstrip(".!").strip()
            if not goal:
                return None
            return ("intent", goal)

    return None
