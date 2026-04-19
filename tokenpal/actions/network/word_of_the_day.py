"""Wordnik word-of-the-day scraped from the public HTML page.

The /word-of-the-day/feed/ RSS endpoint started returning HTTP 400 in 2026.
The HTML page still works — we extract the headword and first definition.
"""

from __future__ import annotations

import html
import re
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_text, scrub_body, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://wordnik.com/word-of-the-day"
# Site rejects empty / non-browser User-Agent on the WOTD page.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TokenPal/1.0; +https://github.com/smabe/TokenPal)"
    ),
}
_HEADWORD_RE = re.compile(
    r'<h1>\s*<a\s+href="/words/[^"]+">([^<]+)</a>\s*</h1>',
    re.IGNORECASE,
)
_DEFINITION_RE = re.compile(
    r'<li>\s*<abbr\s+title="partOfSpeech">([^<]+)</abbr>\s*([^<]+?)\s*</li>',
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return " ".join(html.unescape(value).split()).strip()


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
        text, err = await fetch_text(_URL, headers=_HEADERS)
        if text is None:
            return ActionResult(output=f"Word-of-day fetch failed: {err}", success=False)
        word_match = _HEADWORD_RE.search(text)
        if not word_match:
            return ActionResult(output="Couldn't find headword on WOTD page.", success=False)
        word = _clean(word_match.group(1))
        body = word
        def_match = _DEFINITION_RE.search(text, word_match.end())
        if def_match:
            pos = _clean(def_match.group(1))
            definition = _clean(def_match.group(2))
            if definition:
                body = f"{word} ({pos}): {definition}" if pos else f"{word}: {definition}"
        return ActionResult(
            output=wrap_result(self.action_name, body),
            display_text=scrub_body(body),
        )
