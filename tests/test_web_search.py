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


# ---------------------------------------------------------------------------
# search_many — multi-result DDG Lite scraping
# ---------------------------------------------------------------------------


_DDG_LITE_MULTI_HTML = b"""
<html><body>
<table>
<tr><td class="result-link"></td></tr>
<tr><td><a href="/l/?uddg=https%3A%2F%2Fsite-a.com%2Fpage" class="result-link">Site A Title</a></td></tr>
<tr><td class="result-snippet">Snippet about site A.</td></tr>
<tr><td><a href="/l/?uddg=https%3A%2F%2Fsite-b.com%2Fpage" class="result-link">Site B Title</a></td></tr>
<tr><td class="result-snippet">Snippet about site B.</td></tr>
<tr><td><a href="/l/?uddg=https%3A%2F%2Fsite-c.com%2Fpage" class="result-link">Site C Title</a></td></tr>
<tr><td class="result-snippet">Snippet about site C.</td></tr>
</table>
</body></html>
"""


def _urlopen_returning_body(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return MagicMock(return_value=cm)


def test_search_many_ddg_returns_multiple_results():
    from tokenpal.senses.web_search.client import search_many

    with patch.object(
        client_mod.urllib.request, "urlopen",
        _urlopen_returning_body(_DDG_LITE_MULTI_HTML),
    ):
        results = search_many("best widgets", backend="duckduckgo", limit=5)

    assert len(results) == 3
    assert results[0].title == "Site A Title"
    assert results[0].source_url == "https://site-a.com/page"
    assert "site A" in results[0].text
    assert results[2].source_url == "https://site-c.com/page"
    assert all(r.backend == "duckduckgo" for r in results)


def test_search_many_ddg_honors_limit():
    from tokenpal.senses.web_search.client import search_many

    with patch.object(
        client_mod.urllib.request, "urlopen",
        _urlopen_returning_body(_DDG_LITE_MULTI_HTML),
    ):
        results = search_many("best widgets", backend="duckduckgo", limit=2)

    assert len(results) == 2


def test_search_many_empty_query_returns_empty_list():
    from tokenpal.senses.web_search.client import search_many

    assert search_many("", backend="duckduckgo") == []
    assert search_many("  ", backend="duckduckgo") == []


def test_search_many_wikipedia_wraps_single_result():
    from tokenpal.senses.web_search.client import search_many

    wiki_result = SearchResult(
        query="q", backend="wikipedia",
        title="Wiki", text="body", source_url="https://wiki",
    )
    with patch.object(WikipediaBackend, "search", return_value=wiki_result):
        results = search_many("q", backend="wikipedia")

    assert results == [wiki_result]


def test_search_many_wikipedia_empty_returns_empty_list():
    from tokenpal.senses.web_search.client import search_many

    with patch.object(WikipediaBackend, "search", return_value=None):
        results = search_many("q", backend="wikipedia")

    assert results == []


# ---------------------------------------------------------------------------
# TavilyBackend
# ---------------------------------------------------------------------------


def _mock_tavily_response(results: list[dict[str, Any]]) -> MagicMock:
    """Shape a tavily_search() POST response."""
    return _urlopen_returning({"results": results})


def test_tavily_populates_preloaded_content():
    from tokenpal.senses.web_search.client import TavilyBackend

    tav_body = [{
        "url": "https://example.com/a",
        "title": "Article A",
        "content": "A" * 2000,  # simulate a long extracted body
        "score": 0.92,
    }]
    mocked = _mock_tavily_response(tav_body)
    with patch("urllib.request.urlopen", mocked):
        be = TavilyBackend(api_key="tvly-abcdefghijklmnop")
        hits = be.search_all("something", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.backend == "tavily"
    assert hit.source_url == "https://example.com/a"
    assert hit.title == "Article A"
    # `text` is the short snippet (truncated to _MAX_TEXT_CHARS = 500).
    assert len(hit.text) <= 501  # includes the ellipsis char
    # `preloaded_content` must hold the FULL body, un-truncated.
    assert len(hit.preloaded_content) == 2000
    assert hit.preloaded_content.startswith("A")


def test_tavily_skips_results_without_url_or_content():
    from tokenpal.senses.web_search.client import TavilyBackend

    mocked = _mock_tavily_response([
        {"url": "https://good", "title": "Good", "content": "body here", "score": 1.0},
        {"url": "", "title": "No URL", "content": "body", "score": 0.5},
        {"url": "https://empty", "title": "No content", "content": "", "score": 0.5},
    ])
    with patch("urllib.request.urlopen", mocked):
        be = TavilyBackend(api_key="tvly-keykeykeykeykey123")
        hits = be.search_all("q", limit=5)

    assert len(hits) == 1
    assert hits[0].source_url == "https://good"


def test_tavily_no_key_returns_empty():
    from tokenpal.senses.web_search.client import TavilyBackend

    be = TavilyBackend(api_key="")  # no key, no env var
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("TOKENPAL_TAVILY_KEY", None)
        assert be.search_all("q", limit=5) == []


def test_tavily_network_error_returns_empty():
    from tokenpal.senses.web_search.client import TavilyBackend

    def raiser(*a: Any, **kw: Any) -> Any:
        raise OSError("network down")

    with patch("urllib.request.urlopen", raiser):
        be = TavilyBackend(api_key="tvly-abcdefghijklmnop")
        assert be.search_all("q", limit=5) == []


def test_tavily_malformed_response_returns_empty():
    from tokenpal.senses.web_search.client import TavilyBackend

    # Server returned something that isn't the expected shape.
    mocked = _urlopen_returning({"unexpected": "shape"})
    with patch("urllib.request.urlopen", mocked):
        be = TavilyBackend(api_key="tvly-abcdefghijklmnop")
        assert be.search_all("q", limit=5) == []


def test_search_many_routes_to_tavily_backend():
    from tokenpal.senses.web_search.client import search_many

    mocked = _mock_tavily_response([
        {"url": "https://x", "title": "X", "content": "body", "score": 1.0},
    ])
    with patch("urllib.request.urlopen", mocked):
        hits = search_many("q", backend="tavily", limit=3, tavily_api_key="tvly-keykeykeykeykey")

    assert len(hits) == 1
    assert hits[0].backend == "tavily"
    assert hits[0].preloaded_content == "body"


def test_search_route_tavily_no_key_returns_empty():
    """The dispatcher shouldn't crash when routed to tavily with no key."""
    from tokenpal.senses.web_search.client import search_many

    # TavilyBackend returns [] when api_key is empty — dispatcher passes
    # through cleanly.
    import os
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TOKENPAL_TAVILY_KEY", None)
        hits = search_many("q", backend="tavily", limit=3, tavily_api_key="")
    assert hits == []


def test_preloaded_content_default_is_empty_string():
    """Sanity: backends that don't pre-extract (DDG, Wikipedia) leave
    preloaded_content as the empty string default, signalling to the
    research pipeline that it must fall back to its own fetch."""
    sr = SearchResult(
        query="q", backend="duckduckgo",
        title="t", text="snippet", source_url="https://u",
    )
    assert sr.preloaded_content == ""
