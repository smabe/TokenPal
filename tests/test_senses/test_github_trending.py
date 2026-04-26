"""Tests for the github_trending sense + Search API client."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.senses.github_trending._client import (
    GHRepo,
    _trending_url,
    fetch_top_repo,
)
from tokenpal.senses.github_trending.sense import GitHubTrendingSense


def test_trending_url_uses_7_day_cutoff():
    url = _trending_url(dt.date(2026, 4, 25))
    assert "created:>2026-04-18" in url
    assert "sort=stars" in url
    assert "order=desc" in url
    assert "per_page=1" in url


def test_client_parses_search_response():
    payload = {
        "items": [
            {
                "full_name": "smabe/TokenPal",
                "stargazers_count": 9001,
                "description": "An ASCII desktop buddy",
                "language": "Python",
                "html_url": "https://github.com/smabe/TokenPal",
            }
        ]
    }
    with patch(
        "tokenpal.senses.github_trending._client.http_json", return_value=payload,
    ):
        repo = fetch_top_repo()

    assert repo == GHRepo(
        full_name="smabe/TokenPal",
        stars=9001,
        description="An ASCII desktop buddy",
        language="Python",
        url="https://github.com/smabe/TokenPal",
    )


@pytest.mark.parametrize("payload", [None, {}, {"items": []}, {"items": [{}]}, "x"])
def test_client_returns_none_on_bad_response(payload: Any):
    with patch(
        "tokenpal.senses.github_trending._client.http_json", return_value=payload,
    ):
        assert fetch_top_repo() is None


async def test_poll_emits_summary_with_full_name_stars_lang_desc(
    enabled_config: dict[str, Any],
):
    repo = GHRepo(
        full_name="someone/cool-thing",
        stars=1234,
        description="It does the thing",
        language="Rust",
        url="u",
    )
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repo", return_value=repo,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == (
        "Trending GitHub: someone/cool-thing — 1234 stars (last 7d) (Rust) — It does the thing"
    )


async def test_poll_omits_language_when_missing(enabled_config: dict[str, Any]):
    repo = GHRepo(full_name="x/y", stars=10, description="d", language="", url="")
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repo", return_value=repo,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == "Trending GitHub: x/y — 10 stars (last 7d) — d"


async def test_poll_truncates_long_description(enabled_config: dict[str, Any]):
    repo = GHRepo(full_name="x/y", stars=1, description="A" * 200, language="", url="")
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repo", return_value=repo,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "A" * 200 not in reading.summary
    assert "…" in reading.summary


async def test_poll_filters_sensitive_terms(enabled_config: dict[str, Any]):
    repo = GHRepo(
        full_name="evil/1password-leak", stars=100, description="d", language="", url="",
    )
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repo", return_value=repo,
    ):
        assert await sense.poll() is None


async def test_poll_dedups_unchanged_summary(enabled_config: dict[str, Any]):
    repo = GHRepo(full_name="x/y", stars=1, description="d", language="", url="")
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repo", return_value=repo,
    ):
        first = await sense.poll()
        second = await sense.poll()
    assert first is not None
    assert second is None
