"""Crypto price via CoinGecko public endpoint. Self-throttled to 5 req/min."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.network._base import consent_error, web_fetches_granted
from tokenpal.actions.network._http import fetch_json, wrap_result
from tokenpal.actions.registry import register_action

_URL = "https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"

_SYMBOL_MAP: dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "usdt": "tether",
    "bnb": "binancecoin",
    "sol": "solana",
    "usdc": "usd-coin",
    "xrp": "ripple",
    "doge": "dogecoin",
    "ada": "cardano",
    "trx": "tron",
    "avax": "avalanche-2",
    "shib": "shiba-inu",
    "dot": "polkadot",
    "link": "chainlink",
    "matic": "matic-network",
    "ltc": "litecoin",
    "bch": "bitcoin-cash",
    "xlm": "stellar",
    "atom": "cosmos",
    "etc": "ethereum-classic",
}

_WINDOW_S = 60.0
_MAX_PER_WINDOW = 5
_semaphore = asyncio.Semaphore(_MAX_PER_WINDOW)
_timestamps: deque[float] = deque(maxlen=_MAX_PER_WINDOW)
_lock = asyncio.Lock()


async def _throttle() -> None:
    async with _lock:
        now = time.monotonic()
        while _timestamps and now - _timestamps[0] > _WINDOW_S:
            _timestamps.popleft()
        if len(_timestamps) >= _MAX_PER_WINDOW:
            wait = _WINDOW_S - (now - _timestamps[0])
            if wait > 0:
                await asyncio.sleep(wait)
        _timestamps.append(time.monotonic())


@register_action
class CryptoPriceAction(AbstractAction):
    action_name = "crypto_price"
    description = "Get the current USD price of a cryptocurrency by symbol or CoinGecko id."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Ticker symbol (e.g. 'btc') or CoinGecko id (e.g. 'bitcoin').",
            },
        },
        "required": ["symbol"],
    }
    safe = True
    requires_confirm = False
    consent_category: ClassVar[str] = "web_fetches"

    async def execute(self, **kwargs: Any) -> ActionResult:
        if not web_fetches_granted():
            return consent_error()
        sym = str(kwargs.get("symbol") or "").strip().lower()
        if not sym:
            return ActionResult(output="symbol is required.", success=False)
        coin_id = _SYMBOL_MAP.get(sym, sym)

        async with _semaphore:
            await _throttle()
            data, err = await fetch_json(_URL.format(coin_id=coin_id))

        if data is None or not isinstance(data, dict):
            return ActionResult(output=f"Crypto price fetch failed: {err}", success=False)
        quote = data.get(coin_id)
        if not isinstance(quote, dict):
            return ActionResult(output=f"No price for '{sym}'.", success=False)
        price = quote.get("usd")
        if price is None:
            return ActionResult(output=f"No USD price for '{sym}'.", success=False)
        body = f"{sym.upper()} = ${price} USD"
        return ActionResult(output=wrap_result(self.action_name, body))
