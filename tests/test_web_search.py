"""Tests for the web_search backend clients + router."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tokenpal.senses.web_search import client as client_mod
from tokenpal.senses.web_search.client import (
    LOG_TRUNCATE_CHARS,
    BraveBackend,
    DuckDuckGoBackend,
    SearchResult,
    WikipediaBackend,
    search,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _urlopen_returning(payload: Any) -> MagicMock:
    body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    resp = MagicMock()
    resp.read.return_value = body
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return MagicMock(return_value=cm)


# ---------------------------------------------------------------------------
# LOG_TRUNCATE_CHARS constant
# ---------------------------------------------------------------------------


def test_log_truncate_chars_constant():
    assert LOG_TRUNCATE_CHARS == 80


# ---------------------------------------------------------------------------
# DuckDuckGoBackend
# ---------------------------------------------------------------------------


def test_ddg_parses_abstract_text():
    payload = {
        "AbstractText": "Python is a high-level programming language.",
        "Heading": "Python",
        "AbstractURL": "https://duckduckgo.com/Python",
        "RelatedTopics": [],
    }
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        result = DuckDuckGoBackend().search("python")

    assert result is not None
    assert isinstance(result, SearchResult)
    assert result.backend == "duckduckgo"
    assert result.query == "python"
    assert result.title == "Python"
    assert "high-level programming language" in result.text
    assert result.source_url == "https://duckduckgo.com/Python"


def test_ddg_falls_back_to_related_topics_when_abstract_empty():
    payload = {
        "AbstractText": "",
        "Heading": "",
        "AbstractURL": "",
        "RelatedTopics": [
            {
                "Text": "A fallback topic description from related topics.",
                "FirstURL": "https://duckduckgo.com/rel",
            }
        ],
    }
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        result = DuckDuckGoBackend().search("obscure-thing")

    assert result is not None
    assert "fallback topic description" in result.text
    assert result.source_url == "https://duckduckgo.com/rel"


def test_ddg_returns_none_on_network_failure():
    def raiser(*a: Any, **kw: Any) -> Any:
        raise OSError("boom")

    with patch.object(client_mod.urllib.request, "urlopen", side_effect=raiser):
        assert DuckDuckGoBackend().search("anything") is None


def test_ddg_returns_none_when_both_abstract_and_related_empty():
    payload = {"AbstractText": "", "Heading": "", "AbstractURL": "", "RelatedTopics": []}
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        assert DuckDuckGoBackend().search("nothing") is None


def test_ddg_truncates_long_text_to_500_chars():
    long_text = "x" * 2000
    payload = {"AbstractText": long_text, "Heading": "Long", "AbstractURL": ""}
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        result = DuckDuckGoBackend().search("long")

    assert result is not None
    assert len(result.text) <= 501  # 500 + possible ellipsis char
    assert len(result.text) >= 100  # definitely truncated but still substantial


# ---------------------------------------------------------------------------
# WikipediaBackend
# ---------------------------------------------------------------------------


def test_wikipedia_parses_summary_response():
    payload = {
        "title": "Python (programming language)",
        "extract": "Python is an interpreted, high-level, general-purpose language.",
        "content_urls": {
            "desktop": {"page": "https://en.wikipedia.org/wiki/Python_(programming_language)"},
        },
    }
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        result = WikipediaBackend().search("Python (programming language)")

    assert result is not None
    assert result.backend == "wikipedia"
    assert "interpreted" in result.text
    assert result.title == "Python (programming language)"
    assert "wikipedia.org" in result.source_url


def test_wikipedia_url_encodes_title_with_underscores():
    """The backend replaces spaces with underscores and URL-encodes the result."""
    captured: dict[str, str] = {}

    def fake_urlopen(req: Any, timeout: float = 0) -> Any:
        # req is a urllib.request.Request object
        captured["url"] = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        resp.read.return_value = json.dumps({"extract": "ok", "title": "t"}).encode("utf-8")
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        return cm

    with patch.object(client_mod.urllib.request, "urlopen", side_effect=fake_urlopen):
        WikipediaBackend().search("Alan Turing")

    assert "url" in captured
    # Should have underscores (encoded form of underscore is just underscore —
    # it's a safe char), but no raw spaces or %20.
    assert "Alan_Turing" in captured["url"]
    assert " " not in captured["url"]
    assert "%20" not in captured["url"]


def test_wikipedia_returns_none_on_empty_extract():
    payload = {"title": "Something", "extract": ""}
    with patch.object(client_mod.urllib.request, "urlopen", _urlopen_returning(payload)):
        assert WikipediaBackend().search("something") is None


def test_wikipedia_returns_none_on_network_failure():
    with patch.object(
        client_mod.urllib.request, "urlopen", side_effect=OSError("down")
    ):
        assert WikipediaBackend().search("python") is None


# ---------------------------------------------------------------------------
# BraveBackend
# ---------------------------------------------------------------------------


def test_brave_raises_not_implemented(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TOKENPAL_BRAVE_KEY", raising=False)
    with pytest.raises(NotImplementedError):
        BraveBackend(api_key="somekey").search("anything")


def test_brave_env_var_takes_priority_over_arg(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TOKENPAL_BRAVE_KEY", "env-key-wins")
    be = BraveBackend(api_key="arg-key")
    assert be._api_key == "env-key-wins"


def test_brave_falls_back_to_arg_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TOKENPAL_BRAVE_KEY", raising=False)
    be = BraveBackend(api_key="arg-key")
    assert be._api_key == "arg-key"


# ---------------------------------------------------------------------------
# search() router
# ---------------------------------------------------------------------------


def test_search_default_calls_ddg_first():
    ddg_result = SearchResult(
        query="python", backend="duckduckgo", title="Python", text="a language", source_url=""
    )
    with (
        patch.object(DuckDuckGoBackend, "search", return_value=ddg_result) as ddg,
        patch.object(WikipediaBackend, "search", return_value=None) as wiki,
    ):
        out = search("python")

    assert out is ddg_result
    ddg.assert_called_once()
    # Wikipedia should NOT be consulted when DDG already gave a result.
    wiki.assert_not_called()


def test_search_falls_back_to_wikipedia_when_ddg_empty():
    wiki_result = SearchResult(
        query="python", backend="wikipedia", title="Python", text="from wiki", source_url=""
    )
    with (
        patch.object(DuckDuckGoBackend, "search", return_value=None),
        patch.object(WikipediaBackend, "search", return_value=wiki_result) as wiki,
    ):
        out = search("python")

    assert out is wiki_result
    wiki.assert_called_once()


def test_search_returns_none_when_all_backends_fail():
    with (
        patch.object(DuckDuckGoBackend, "search", return_value=None),
        patch.object(WikipediaBackend, "search", return_value=None),
    ):
        assert search("nothing") is None


def test_search_never_raises_on_network_errors():
    """Even when every backend raises, router must swallow and return None."""

    def raiser(self: Any, q: str) -> Any:
        raise OSError("network unreachable")

    with (
        patch.object(DuckDuckGoBackend, "search", raiser),
        patch.object(WikipediaBackend, "search", raiser),
    ):
        # Must not raise.
        assert search("anything") is None


def test_search_empty_query_returns_none():
    assert search("") is None
    assert search("   ") is None


def test_search_explicit_wikipedia_backend():
    wiki_result = SearchResult(
        query="turing", backend="wikipedia", title="Turing", text="...", source_url=""
    )
    with (
        patch.object(WikipediaBackend, "search", return_value=wiki_result),
        patch.object(DuckDuckGoBackend, "search", return_value=None) as ddg,
    ):
        out = search("turing", backend="wikipedia")

    assert out is wiki_result
    ddg.assert_not_called()


def test_search_brave_backend_raises_not_implemented(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TOKENPAL_BRAVE_KEY", raising=False)
    with pytest.raises(NotImplementedError):
        search("anything", backend="brave", brave_api_key="k")
