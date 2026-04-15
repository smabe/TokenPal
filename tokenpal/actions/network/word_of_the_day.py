"""Wordnik word-of-the-day via RSS. Free JSON API still requires a key."""

from __future__ import annotations

import re
from typing import Any, ClassVar

import feedparser

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_text, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://wordnik.com/word-of-the-day/feed/"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    return " ".join(_TAG_RE.sub("", value).split()).strip()


@register_action
class WordOfTheDayAction(AbstractAction):
    action_name = "word_of_the_day"
    description = "Get today's Wordnik word of the day with definition."
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        text, err = await fetch_text(_URL)
        if text is None:
            return ActionResult(output=f"Word-of-day fetch failed: {err}", success=False)
        parsed = feedparser.parse(text)
        entries = getattr(parsed, "entries", []) or []
        if not entries:
            return ActionResult(output="RSS had no entries.", success=False)
        first = entries[0]
        title = _strip_html(str(getattr(first, "title", "") or ""))
        description = _strip_html(str(getattr(first, "description", "") or ""))
        if not title:
            return ActionResult(output="RSS entry missing title.", success=False)
        body = f"{title}: {description}" if description else title
        return ActionResult(output=wrap_result(self.action_name, body))
