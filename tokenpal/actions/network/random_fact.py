"""Random trivia fact via uselessfacts.jsph.pl."""

from __future__ import annotations

from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://uselessfacts.jsph.pl/api/v2/facts/random?language=en"


@register_action
class RandomFactAction(AbstractAction):
    action_name = "random_fact"
    description = "Fetch a random trivia fact."
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        data, err = await fetch_json(_URL)
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Fact fetch failed: {err}", success=False)
        text = str(data.get("text") or "").strip()
        if not text:
            return ActionResult(output="No fact returned.", success=False)
        return ActionResult(output=wrap_result(self.action_name, text))
