"""Tests for the fetch_url action."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.actions.research.fetch_url import FetchUrlAction


@pytest.fixture()
def grant_consent(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)
    consent_mod.save_consent({consent_mod.Category.WEB_FETCHES: True}, path)
    yield


@pytest.mark.asyncio
async def test_rejects_empty_url() -> None:
    action = FetchUrlAction({})
    result = await action.execute(url="")
    assert result.success is False
    assert "empty URL" in result.output


@pytest.mark.asyncio
async def test_rejects_bad_scheme() -> None:
    action = FetchUrlAction({})
    result = await action.execute(url="file:///etc/passwd")
    assert result.success is False
    assert "bad URL" in result.output


@pytest.mark.asyncio
async def test_without_consent_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com")
    assert result.success is False
    assert "consent" in result.output.lower()


@pytest.mark.asyncio
async def test_wraps_extracted_text(monkeypatch: pytest.MonkeyPatch, grant_consent) -> None:
    async def fake_fetch_and_extract(_url: str, **_kw: Any) -> str:
        return "Article body here."

    monkeypatch.setattr(
        "tokenpal.actions.research.fetch_url.fetch_and_extract",
        fake_fetch_and_extract,
    )

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com/story")
    assert result.success is True
    assert "<tool_result" in result.output
    assert "Article body here." in result.output


@pytest.mark.asyncio
async def test_sensitive_or_unusable_reports_gracefully(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    """Both sensitive filtering and unreachable URL share the 'nothing usable'
    error surface — fetch_and_extract returns None either way."""
    async def none_fetch(_url: str, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(
        "tokenpal.actions.research.fetch_url.fetch_and_extract", none_fetch
    )

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com")
    assert result.success is False
    assert "nothing usable" in result.output


@pytest.mark.asyncio
async def test_fetch_and_extract_filters_sensitive_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check the sensitive-term filter is inside fetch_and_extract, not the action."""
    from tokenpal.actions.research.fetch_url import fetch_and_extract

    async def sensitive_fetch(_url: str) -> str:
        return "<html></html>"

    def sensitive_extract(_html: str, _url: str) -> str:
        return "Open your 1Password vault and paste the password."

    monkeypatch.setattr("tokenpal.actions.research.fetch_url._fetch", sensitive_fetch)
    monkeypatch.setattr(
        "tokenpal.actions.research.fetch_url._extract", sensitive_extract
    )

    result = await fetch_and_extract("https://example.com")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_and_extract_timeout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from tokenpal.actions.research.fetch_url import fetch_and_extract

    async def slow_fetch(_url: str) -> str:
        await asyncio.sleep(10)
        return "never"

    monkeypatch.setattr("tokenpal.actions.research.fetch_url._fetch", slow_fetch)
    result = await fetch_and_extract("https://example.com", timeout_s=0.05)
    assert result is None
