"""Shared HTTP-JSON helper for outbound polling.

All keyless network calls in TokenPal route through this helper so the
error-handling, timeout, and User-Agent posture stays consistent.
Returns None on any network, HTTP, parse, or schema failure — callers
never see exceptions from this module.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "TokenPal/1.0 (+https://github.com/smabe/TokenPal)"


def http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
) -> Any:
    """Issue an HTTP request and parse the response as JSON.

    Returns the parsed JSON object on success, or None on any failure.
    Never raises. Callers should type-narrow the return value themselves.
    """
    merged: dict[str, str] = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=body, headers=merged, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        log.debug("http %s %s: %s", method, e.code, e.reason)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.debug("http %s network failure: %s", method, e)
        return None
    except Exception as e:  # noqa: BLE001 — network code must never raise
        log.debug("http %s unexpected error: %s", method, e)
        return None

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.debug("http %s response parse failed: %s", method, e)
        return None
