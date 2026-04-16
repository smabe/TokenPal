"""search_web — async wrapper around the existing /ask search backends.

Exposes DuckDuckGo + Wikipedia + (stub) Brave as an LLM-callable tool.
The underlying client in senses.web_search.client is synchronous (urllib),
so we run it in a thread executor to avoid blocking the agent loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, get_args

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_term
from tokenpal.config.consent import Category, has_consent
from tokenpal.senses.web_search.client import BackendName, search

log = logging.getLogger(__name__)


@register_action
class SearchWebAction(AbstractAction):
    action_name = "search_web"
    description = (
        "Search the web (DuckDuckGo or Wikipedia) for a single query. "
        "Returns one summary + source URL or an error."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "backend": {
                "type": "string",
                "enum": ["duckduckgo", "wikipedia"],
                "description": "Defaults to duckduckgo.",
            },
        },
        "required": ["query"],
    }
    platforms: ClassVar[tuple[str, ...]] = ("windows", "darwin", "linux")
    safe: ClassVar[bool] = True
    requires_confirm: ClassVar[bool] = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ActionResult(output="search_web: empty query", success=False)
        if not has_consent(Category.WEB_FETCHES):
            return ActionResult(
                output="search_web: web_fetches consent not granted. Run /consent.",
                success=False,
            )

        backend: BackendName = _coerce_backend(kwargs.get("backend"))
        result = await asyncio.to_thread(search, query, backend=backend)
        if result is None:
            return ActionResult(
                output=f"search_web: no result for '{query[:80]}'",
                success=False,
            )
        if contains_sensitive_term(result.text) or contains_sensitive_term(result.title):
            log.debug("search_web: result filtered (sensitive) for %s", query[:80])
            return ActionResult(
                output="search_web: result filtered (sensitive term)",
                success=False,
            )

        body = (
            f"<tool_result tool=\"search_web\" backend=\"{result.backend}\" "
            f"url=\"{result.source_url}\">\n"
            f"{result.title}\n{result.text}\n"
            f"</tool_result>"
        )
        return ActionResult(output=body, success=True, display_url=result.source_url)


# Brave lives in BackendName but its backend is stub-only (NotImplementedError),
# so search_web refuses to route to it. Keep the BackendName source of truth by
# reading the Literal's members at runtime.
_ALLOWED_BACKENDS: frozenset[str] = frozenset(get_args(BackendName)) - {"brave"}


def _coerce_backend(raw: Any) -> BackendName:
    name = (raw or "duckduckgo").lower()
    if name in _ALLOWED_BACKENDS:
        return name  # type: ignore[return-value]
    return "duckduckgo"
