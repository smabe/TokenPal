"""Random recipe via TheMealDB test key (1)."""

from __future__ import annotations

import random
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, scrub_body, wrap_result
from tokenpal.actions.registry import register_action

_RANDOM_URL = "https://www.themealdb.com/api/json/v1/1/random.php"
_FILTER_URL = "https://www.themealdb.com/api/json/v1/1/filter.php?i={ingredient}"
_LOOKUP_URL = "https://www.themealdb.com/api/json/v1/1/lookup.php?i={meal_id}"


def _format_meal(meal: dict[str, Any]) -> str:
    name = str(meal.get("strMeal") or "").strip()
    area = str(meal.get("strArea") or "").strip()
    category = str(meal.get("strCategory") or "").strip()
    instructions = str(meal.get("strInstructions") or "").strip()
    if len(instructions) > 400:
        instructions = instructions[:397].rstrip() + "..."
    header = name
    if area or category:
        header = f"{name} ({category}{', ' + area if area else ''})"
    return f"{header}\n{instructions}" if instructions else header


@register_action
class RandomRecipeAction(AbstractAction):
    action_name = "random_recipe"
    description = "Get a random recipe, optionally filtered by main ingredient."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "ingredient": {
                "type": "string",
                "description": "Optional main ingredient (e.g. 'chicken').",
            },
        },
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        ingredient = kwargs.get("ingredient")
        if ingredient and isinstance(ingredient, str) and ingredient.strip():
            safe_ing = ingredient.strip().replace(" ", "_")
            data, err = await fetch_json(_FILTER_URL.format(ingredient=safe_ing))
            if data is None or not isinstance(data, dict):
                return ActionResult(output=f"Recipe filter failed: {err}", success=False)
            meals = data.get("meals") or []
            if not meals:
                return ActionResult(
                    output=f"No recipes found for '{ingredient}'.",
                    success=False,
                )
            picked = random.choice(meals)
            meal_id = picked.get("idMeal")
            if not meal_id:
                return ActionResult(output="Recipe id missing.", success=False)
            detail, d_err = await fetch_json(_LOOKUP_URL.format(meal_id=meal_id))
            if detail is None or not isinstance(detail, dict):
                return ActionResult(output=f"Recipe lookup failed: {d_err}", success=False)
            detail_meals = detail.get("meals") or []
            if not detail_meals:
                return ActionResult(output="Recipe detail empty.", success=False)
            meal = detail_meals[0]
        else:
            data, err = await fetch_json(_RANDOM_URL)
            if data is None or not isinstance(data, dict):
                return ActionResult(output=f"Random recipe failed: {err}", success=False)
            meals = data.get("meals") or []
            if not meals:
                return ActionResult(output="No random meal returned.", success=False)
            meal = meals[0]

        body = _format_meal(meal)
        return ActionResult(
            output=wrap_result(self.action_name, body),
            display_text=scrub_body(body),
        )
