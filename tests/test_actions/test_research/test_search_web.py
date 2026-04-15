"""Tests for the search_web action."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.actions.research.search_web import SearchWebAction
from tokenpal.senses.web_search.client import SearchResult


@pytest.fixture()
def grant_consent(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)
    consent_mod.save_consent({consent_mod.Category.WEB_FETCHES: True}, path)
    yield


@pytest.mark.asyncio
async def test_rejects_empty_query() -> None:
    action = SearchWebAction({})
    result = await action.execute(query="")
    assert result.success is False
    assert "empty query" in result.output


@pytest.mark.asyncio
async def test_without_consent_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)

    action = SearchWebAction({})
    result = await action.execute(query="ignored")
    assert result.success is False
    assert "consent" in result.output.lower()


@pytest.mark.asyncio
async def test_no_result_reports_gracefully(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.research.search_web.search",
        lambda *_args, **_kw: None,
    )
    action = SearchWebAction({})
    result = await action.execute(query="unknown")
    assert result.success is False
    assert "no result" in result.output


@pytest.mark.asyncio
async def test_happy_path_wraps_result(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    def fake_search(q: str, backend: str = "duckduckgo", **_: Any) -> SearchResult:
        return SearchResult(
            query=q,
            backend=backend,  # type: ignore[arg-type]
            title="Example",
            text="Example body text",
            source_url="https://example.com",
        )

    monkeypatch.setattr("tokenpal.actions.research.search_web.search", fake_search)
    action = SearchWebAction({})
    result = await action.execute(query="what is x", backend="wikipedia")
    assert result.success is True
    assert "<tool_result" in result.output
    assert "Example body text" in result.output
    assert "https://example.com" in result.output


@pytest.mark.asyncio
async def test_unknown_backend_falls_back_to_ddg(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    captured: dict[str, Any] = {}

    def fake_search(q: str, backend: str = "duckduckgo", **_: Any) -> SearchResult | None:
        captured["backend"] = backend
        return None

    monkeypatch.setattr("tokenpal.actions.research.search_web.search", fake_search)
    action = SearchWebAction({})
    await action.execute(query="q", backend="made-up")
    assert captured["backend"] == "duckduckgo"


@pytest.mark.asyncio
async def test_sensitive_result_filtered(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    def fake_search(*_args: Any, **_kw: Any) -> SearchResult:
        return SearchResult(
            query="q",
            backend="duckduckgo",
            title="Safe title",
            text="How to unlock the 1Password vault without the password",
            source_url="https://example.com",
        )

    monkeypatch.setattr("tokenpal.actions.research.search_web.search", fake_search)
    action = SearchWebAction({})
    result = await action.execute(query="x")
    assert result.success is False
    assert "filtered" in result.output
