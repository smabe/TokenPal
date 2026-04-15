"""Team's last events via TheSportsDB test key (3).

Livescore is Patreon-gated — we only expose last-events which is free.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import quote_plus

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_SEARCH_URL = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={team}"
_EVENTS_URL = "https://www.thesportsdb.com/api/v1/json/3/eventslast.php?id={team_id}"
_MAX_EVENTS = 3


def _format_event(ev: dict[str, Any]) -> str:
    date = str(ev.get("dateEvent") or "").strip()
    home = str(ev.get("strHomeTeam") or "").strip()
    away = str(ev.get("strAwayTeam") or "").strip()
    h_score = ev.get("intHomeScore")
    a_score = ev.get("intAwayScore")
    if h_score is not None and a_score is not None:
        return f"{date}: {home} {h_score} - {a_score} {away}"
    return f"{date}: {home} vs {away} (no score)"


@register_action
class SportsScoreAction(AbstractAction):
    action_name = "sports_score"
    description = "Get the most recent results for a sports team by name."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "team": {"type": "string", "description": "Team name (e.g. 'Arsenal')."},
        },
        "required": ["team"],
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        team = str(kwargs.get("team") or "").strip()
        if not team:
            return ActionResult(output="team is required.", success=False)

        data, err = await fetch_json(_SEARCH_URL.format(team=quote_plus(team)))
        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Team search failed: {err}", success=False)
        teams = data.get("teams") or []
        if not teams:
            return ActionResult(output=f"No team found matching '{team}'.", success=False)
        first = teams[0]
        team_id = str(first.get("idTeam") or "").strip()
        if not team_id:
            return ActionResult(output="Team had no id.", success=False)

        ev_data, e_err = await fetch_json(_EVENTS_URL.format(team_id=team_id))
        if ev_data is None or not isinstance(ev_data, dict):
            return ActionResult(output=f"Events fetch failed: {e_err}", success=False)
        events = ev_data.get("results") or []
        if not events:
            return ActionResult(output=f"No recent events for '{team}'.", success=False)
        lines = [_format_event(ev) for ev in events[:_MAX_EVENTS] if isinstance(ev, dict)]
        return ActionResult(output=wrap_result(self.action_name, "\n".join(lines)))
