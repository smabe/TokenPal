"""Shared fixtures for phase 2b network tool tests.

Every test runs with consent granted by default (monkeypatched). Individual
consent-denied tests override with ``granted=False``. We patch ``has_consent``
at the point of use in ``_base.web_fetches_granted`` to avoid filesystem I/O.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

_TOOL_MODULES = (
    "tokenpal.actions.network.currency",
    "tokenpal.actions.network.weather_forecast_week",
    "tokenpal.actions.network.air_quality",
    "tokenpal.actions.network.random_fact",
    "tokenpal.actions.network.joke_of_the_day",
    "tokenpal.actions.network.word_of_the_day",
    "tokenpal.actions.network.on_this_day",
    "tokenpal.actions.network.random_recipe",
    "tokenpal.actions.network.trivia_question",
    "tokenpal.actions.network.sports_score",
    "tokenpal.actions.network.crypto_price",
    "tokenpal.actions.network.book_suggestion",
)


def _set_grant(monkeypatch: pytest.MonkeyPatch, granted: bool) -> None:
    import importlib

    for mod_path in _TOOL_MODULES:
        mod = importlib.import_module(mod_path)
        if hasattr(mod, "web_fetches_granted"):
            monkeypatch.setattr(mod, "web_fetches_granted", lambda g=granted: g)


@pytest.fixture(autouse=True)
def _grant_consent(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _set_grant(monkeypatch, True)
    yield


@pytest.fixture
def deny_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_grant(monkeypatch, False)
