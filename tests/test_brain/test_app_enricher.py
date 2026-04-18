"""Tests for AppEnricher — cache, gating, fetch path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tokenpal.brain.app_enricher import (
    MAX_DESCRIPTION_CHARS,
    AppEnricher,
    _trim_to_sentence,
)
from tokenpal.brain.memory import MemoryStore
from tokenpal.senses.web_search.client import SearchResult


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "enricher.db")
    s.setup()
    return s


@pytest.fixture()
def enricher(store: MemoryStore) -> AppEnricher:
    return AppEnricher(memory=store, sensitive_apps={"1Password", "Banking"})


def _patch_search(
    monkeypatch: pytest.MonkeyPatch,
    result: SearchResult | None,
    calls: list[str] | None = None,
) -> None:
    def fake_search(query: str, **kwargs: Any) -> SearchResult | None:
        if calls is not None:
            calls.append(query)
        return result

    monkeypatch.setattr(
        "tokenpal.brain.app_enricher.search", fake_search,
    )


def _grant_consent(monkeypatch: pytest.MonkeyPatch, granted: bool = True) -> None:
    monkeypatch.setattr(
        "tokenpal.brain.app_enricher.has_consent", lambda _: granted,
    )


def _mk_result(text: str = "Cronometer is a nutrition and calorie tracking app.") -> SearchResult:
    return SearchResult(
        query="q", backend="duckduckgo", title="Cronometer",
        text=text, source_url="https://example.com",
    )


async def test_cached_description_returned_synchronously(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.put_app_enrichment("Cronometer", "A nutrition tracker.", success=True)
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch)

    got = await enricher.enrich("Cronometer")
    assert got == "A nutrition tracker."
    assert calls == []  # no network call when cached


async def test_first_sighting_fetches_and_caches(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, _mk_result())
    _grant_consent(monkeypatch)

    got = await enricher.enrich("Cronometer")
    assert got == "Cronometer is a nutrition and calorie tracking app."
    cached = store.get_app_enrichment("Cronometer")
    assert cached is not None
    description, _age, success = cached
    assert success is True
    assert description == "Cronometer is a nutrition and calorie tracking app."


async def test_sensitive_app_never_enriched(
    enricher: AppEnricher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch)

    assert await enricher.enrich("1Password") is None
    assert calls == []


async def test_non_app_filter_skips_window_server(
    enricher: AppEnricher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch)

    assert await enricher.enrich("Finder") is None
    assert await enricher.enrich("WindowServer") is None
    assert calls == []


async def test_no_consent_does_not_fetch(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch, granted=False)

    assert await enricher.enrich("Cronometer") is None
    assert calls == []
    assert store.get_app_enrichment("Cronometer") is None


async def test_failed_fetch_caches_failure(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, None)
    _grant_consent(monkeypatch)

    assert await enricher.enrich("NeverHeardOfIt") is None
    cached = store.get_app_enrichment("NeverHeardOfIt")
    assert cached is not None
    description, _age, success = cached
    assert success is False
    assert description is None


async def test_recent_failure_does_not_refetch(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.put_app_enrichment("NeverHeardOfIt", None, success=False)
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch)

    assert await enricher.enrich("NeverHeardOfIt") is None
    assert calls == []  # recent failure short-circuits


async def test_sensitive_term_in_result_filtered(
    enricher: AppEnricher, store: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_search(monkeypatch, _mk_result(text="Try Venmo for payments."))
    _grant_consent(monkeypatch)

    assert await enricher.enrich("SomeApp") is None
    cached = store.get_app_enrichment("SomeApp")
    assert cached is not None
    _, _, success = cached
    assert success is False


def test_trim_to_sentence_respects_max() -> None:
    long = "This is a very long description. " + "x" * 500
    trimmed = _trim_to_sentence(long)
    assert trimmed == "This is a very long description."

    single_long = "x" * 300
    trimmed = _trim_to_sentence(single_long)
    assert len(trimmed) <= MAX_DESCRIPTION_CHARS + 1  # +1 for the ellipsis char
    assert trimmed.endswith("…")


def test_trim_to_sentence_handles_exclamation_and_question() -> None:
    assert _trim_to_sentence("First! Second.") == "First!"
    assert _trim_to_sentence("What? Another.") == "What?"


async def test_in_flight_dedup_shares_fetch(
    enricher: AppEnricher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent enrich() calls for the same app share one search_web call."""
    import asyncio

    calls: list[str] = []

    def slow_search(query: str, **kwargs: Any) -> SearchResult | None:
        calls.append(query)
        return _mk_result()

    monkeypatch.setattr(
        "tokenpal.brain.app_enricher.search", slow_search,
    )
    _grant_consent(monkeypatch)

    a, b = await asyncio.gather(
        enricher.enrich("Cronometer"),
        enricher.enrich("Cronometer"),
    )
    assert a == b
    assert len(calls) == 1


async def test_empty_app_name_is_gated(
    enricher: AppEnricher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_search(monkeypatch, _mk_result(), calls=calls)
    _grant_consent(monkeypatch)

    assert await enricher.enrich("") is None
    assert await enricher.enrich("   ") is None
    assert calls == []
