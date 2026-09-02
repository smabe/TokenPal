"""Small helper shared by every phase 2b tool."""

from __future__ import annotations

from tokenpal.actions.base import ActionResult
from tokenpal.actions.base import consent_error as base_consent_error
from tokenpal.config.consent import Category, has_consent


def consent_error() -> ActionResult:
    """Uniform error when the user hasn't granted the web_fetches category."""
    return base_consent_error("web fetches")


def web_fetches_granted() -> bool:
    return has_consent(Category.WEB_FETCHES)
