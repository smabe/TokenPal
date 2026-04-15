"""Book suggestion via Google Books keyless endpoint."""

from __future__ import annotations

import random
from typing import Any, ClassVar
from urllib.parse import quote_plus

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_URL = (
    "https://www.googleapis.com/books/v1/volumes"
    "?q=subject:{genre}&maxResults=5&orderBy=relevance"
)


def _format_book(item: dict[str, Any]) -> str | None:
    info = item.get("volumeInfo")
    if not isinstance(info, dict):
        return None
    title = str(info.get("title") or "").strip()
    if not title:
        return None
    authors = info.get("authors") or []
    if isinstance(authors, list):
        author_str = ", ".join(str(a) for a in authors) or "unknown author"
    else:
        author_str = "unknown author"
    desc = str(info.get("description") or "").strip().splitlines()
    one_line = desc[0] if desc else ""
    if len(one_line) > 200:
        one_line = one_line[:197].rstrip() + "..."
    return f"'{title}' by {author_str}{': ' + one_line if one_line else ''}"


@register_action
class BookSuggestionAction(AbstractAction):
    action_name = "book_suggestion"
    description = "Suggest a book by genre via Google Books."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "genre": {"type": "string", "description": "Genre subject (e.g. 'mystery')."},
        },
        "required": ["genre"],
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        genre = str(kwargs.get("genre") or "").strip()
        if not genre:
            return ActionResult(output="genre is required.", success=False)
        data, err = await fetch_json(_URL.format(genre=quote_plus(genre)))
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Book fetch failed: {err}", success=False)
        items = data.get("items") or []
        if not items:
            return ActionResult(output=f"No books found for '{genre}'.", success=False)
        formatted = [f for f in (_format_book(item) for item in items) if f]
        if not formatted:
            return ActionResult(output="Books returned without usable metadata.", success=False)
        pick = random.choice(formatted)
        return ActionResult(output=wrap_result(self.action_name, pick))
