"""Phase 2b keyless-network utility tools.

Every module here registers exactly one ``@register_action``. Shared HTTP
plumbing lives in ``_http.py``. All tools declare ``consent_category``
``web_fetches`` and short-circuit with an error ActionResult if the user
hasn't granted that consent.
"""
