"""Dad joke via icanhazdadjoke.com. Both Accept + User-Agent headers required."""

from __future__ import annotations

from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, scrub_body, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://icanhazdadjoke.com/"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "TokenPal (https://github.com/smabe/TokenPal)",
}


@register_action
class JokeOfTheDayAction(AbstractAction):
    action_name = "joke_of_the_day"
    description = "Fetch a random dad joke."
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        data, err = await fetch_json(_URL, headers=_HEADERS)
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Joke fetch failed: {err}", success=False)
        joke = str(data.get("joke") or "").strip()
        if not joke:
            return ActionResult(output="No joke returned.", success=False)
        return ActionResult(
            output=wrap_result(self.action_name, joke),
            display_text=scrub_body(joke),
        )
