"""Historical events for today via Wikimedia on-this-day API."""

from __future__ import annotations

import datetime as _dt
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/{mm}/{dd}"
_HEADERS = {
    "User-Agent": (
        "TokenPal/1.0 (https://github.com/smabe/TokenPal; abraham.awadallah@gmail.com)"
    ),
}
_MAX_EVENTS = 5


@register_action
class OnThisDayAction(AbstractAction):
    action_name = "on_this_day"
    description = "Get historical events that happened on today's date."
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        today = _dt.date.today()
        url = _URL.format(mm=f"{today.month:02d}", dd=f"{today.day:02d}")
        data, err = await fetch_json(url, headers=_HEADERS)
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"On-this-day fetch failed: {err}", success=False)
        events = data.get("events") or []
        if not events:
            return ActionResult(output="No events found for today.", success=False)
        lines = []
        for ev in events[:_MAX_EVENTS]:
            if not isinstance(ev, dict):
                continue
            year = ev.get("year", "?")
            text = str(ev.get("text") or "").strip()
            if text:
                lines.append(f"{year}: {text}")
        if not lines:
            return ActionResult(output="Events had no usable text.", success=False)
        return ActionResult(output=wrap_result(self.action_name, "\n".join(lines)))
