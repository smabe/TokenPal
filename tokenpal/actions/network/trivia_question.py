"""Trivia question via OpenTDB with session-token caching + rate limit."""

from __future__ import annotations

import asyncio
import html
import time
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_TOKEN_URL = "https://opentdb.com/api_token.php?command=request"
_QUESTION_URL = "https://opentdb.com/api.php?amount=1&type=multiple"

# Top 20 OpenTDB category IDs (hardcoded — OpenTDB's list is stable).
_CATEGORY_MAP: dict[str, int] = {
    "general": 9,
    "books": 10,
    "film": 11,
    "music": 12,
    "theatre": 13,
    "television": 14,
    "video games": 15,
    "board games": 16,
    "science": 17,
    "computers": 18,
    "math": 19,
    "mythology": 20,
    "sports": 21,
    "geography": 22,
    "history": 23,
    "politics": 24,
    "art": 25,
    "celebrities": 26,
    "animals": 27,
    "vehicles": 28,
}

_MIN_SPACING_S = 5.0
_lock = asyncio.Lock()
_last_call_ts: float = 0.0
_cached_token: str | None = None


async def _get_token() -> str | None:
    global _cached_token
    if _cached_token is not None:
        return _cached_token
    data, _ = await fetch_json(_TOKEN_URL)
    if data and isinstance(data, dict) and data.get("response_code") == 0:
        token = data.get("token")
        if isinstance(token, str) and token:
            _cached_token = token
            return token
    return None


def _resolve_category(raw: Any) -> int | None:
    if not raw:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return _CATEGORY_MAP.get(raw.strip().lower())
    return None


def _format_question(q: dict[str, Any]) -> str:
    question = html.unescape(str(q.get("question") or ""))
    correct = html.unescape(str(q.get("correct_answer") or ""))
    incorrect = [html.unescape(str(a)) for a in (q.get("incorrect_answers") or [])]
    choices = incorrect + [correct]
    choices_str = " / ".join(choices)
    return f"Q: {question}\nChoices: {choices_str}\nA: {correct}"


@register_action
class TriviaQuestionAction(AbstractAction):
    action_name = "trivia_question"
    description = "Fetch a multiple-choice trivia question, optionally by category name."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": f"One of: {', '.join(sorted(_CATEGORY_MAP))}.",
            },
        },
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        global _last_call_ts
        async with _lock:
            elapsed = time.monotonic() - _last_call_ts
            if elapsed < _MIN_SPACING_S and _last_call_ts != 0.0:
                await asyncio.sleep(_MIN_SPACING_S - elapsed)
            _last_call_ts = time.monotonic()

            token = await _get_token()
            url = _QUESTION_URL
            cat_id = _resolve_category(kwargs.get("category"))
            if cat_id is not None:
                url += f"&category={cat_id}"
            if token:
                url += f"&token={token}"

            data, err = await fetch_json(url)

        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Trivia fetch failed: {err}", success=False)
        code = data.get("response_code")
        if code != 0:
            return ActionResult(output=f"OpenTDB returned code {code}.", success=False)
        results = data.get("results") or []
        if not results:
            return ActionResult(output="No trivia question returned.", success=False)
        return ActionResult(output=wrap_result(self.action_name, _format_question(results[0])))
