"""Tests for the fetch_url action."""

from __future__ import annotations

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
    async def fake_fetch(_url: str) -> str:
        return "<html><body><p>Article body here.</p></body></html>"

    def fake_extract(_html: str, _url: str) -> str:
        return "Article body here."

    monkeypatch.setattr("tokenpal.actions.research.fetch_url._fetch", fake_fetch)
    monkeypatch.setattr("tokenpal.actions.research.fetch_url._extract", fake_extract)

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com/story")
    assert result.success is True
    assert "<tool_result" in result.output
    assert "Article body here." in result.output


@pytest.mark.asyncio
async def test_sensitive_content_filtered(
    monkeypatch: pytest.MonkeyPatch, grant_consent
) -> None:
    async def fake_fetch(_url: str) -> str:
        return "<html></html>"

    def fake_extract(_html: str, _url: str) -> str:
        # Contains a sensitive term from tokenpal.brain.personality.
        return "Open your 1Password vault and paste the password."

    monkeypatch.setattr("tokenpal.actions.research.fetch_url._fetch", fake_fetch)
    monkeypatch.setattr("tokenpal.actions.research.fetch_url._extract", fake_extract)

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com")
    assert result.success is False
    assert "filtered" in result.output


@pytest.mark.asyncio
async def test_timeout_returns_error(monkeypatch: pytest.MonkeyPatch, grant_consent) -> None:
    import asyncio

    async def slow_fetch(_url: str) -> str:
        await asyncio.sleep(10)
        return "never"

    monkeypatch.setattr("tokenpal.actions.research.fetch_url._fetch", slow_fetch)
    monkeypatch.setattr(
        "tokenpal.actions.research.fetch_url._DEFAULT_TIMEOUT_S", 0.05
    )

    action = FetchUrlAction({})
    result = await action.execute(url="https://example.com")
    assert result.success is False
    assert "timed out" in result.output
