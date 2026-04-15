"""Currency conversion via open.er-api.com with frankfurter.app fallback."""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

log = logging.getLogger(__name__)

_CACHE_TTL_S = 600.0  # 10 minutes per base currency
_PRIMARY_URL = "https://open.er-api.com/v6/latest/{base}"
_FALLBACK_URL = "https://api.frankfurter.app/latest?from={base}&to={target}"

# base -> (timestamp, {target: rate})
_rate_cache: dict[str, tuple[float, dict[str, float]]] = {}


def _cached_rates(base: str) -> dict[str, float] | None:
    entry = _rate_cache.get(base)
    if entry is None:
        return None
    ts, rates = entry
    if time.monotonic() - ts > _CACHE_TTL_S:
        return None
    return rates


def _store_rates(base: str, rates: dict[str, float]) -> None:
    _rate_cache[base] = (time.monotonic(), rates)


async def _fetch_rate(base: str, target: str) -> tuple[float | None, str | None]:
    cached = _cached_rates(base)
    if cached is not None and target in cached:
        return cached[target], None

    data, err = await fetch_json(_PRIMARY_URL.format(base=base))
    if data and isinstance(data, dict) and data.get("result") == "success":
        rates = data.get("rates") or {}
        if isinstance(rates, dict):
            float_rates = {k: float(v) for k, v in rates.items() if isinstance(v, (int, float))}
            _store_rates(base, float_rates)
            if target in float_rates:
                return float_rates[target], None

    data, err2 = await fetch_json(_FALLBACK_URL.format(base=base, target=target))
    if data and isinstance(data, dict):
        rates = data.get("rates") or {}
        if isinstance(rates, dict) and target in rates:
            return float(rates[target]), None
    combined = err or err2 or "rate unavailable"
    return None, combined


@register_action
class CurrencyAction(AbstractAction):
    action_name = "currency"
    description = "Convert an amount between two currency codes (ISO 4217)."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "Amount to convert."},
            "from_code": {"type": "string", "description": "Source currency (e.g. USD)."},
            "to_code": {"type": "string", "description": "Target currency (e.g. EUR)."},
        },
        "required": ["amount", "from_code", "to_code"],
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        try:
            amount = float(kwargs["amount"])
            from_code = str(kwargs["from_code"]).upper().strip()
            to_code = str(kwargs["to_code"]).upper().strip()
        except (KeyError, TypeError, ValueError):
            return ActionResult(output="amount, from_code, to_code are required.", success=False)
        if not from_code.isalpha() or not to_code.isalpha():
            return ActionResult(output="Currency codes must be letters only.", success=False)

        if from_code == to_code:
            body = f"{amount:.2f} {from_code} = {amount:.2f} {to_code}"
            return ActionResult(output=wrap_result(self.action_name, body))

        rate, err = await _fetch_rate(from_code, to_code)
        if rate is None:
            return ActionResult(
                output=f"Currency lookup failed: {err}",
                success=False,
            )
        converted = amount * rate
        body = f"{amount:.2f} {from_code} = {converted:.2f} {to_code} (rate {rate:.4f})"
        return ActionResult(output=wrap_result(self.action_name, body))
