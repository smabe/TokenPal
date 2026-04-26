"""Tests for the github_trending sense + Search API client."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.senses.github_trending._client import (
    GHRepo,
    _trending_url,
    fetch_top_repos,
)
from tokenpal.senses.github_trending.sense import GitHubTrendingSense


def test_trending_url_uses_7_day_cutoff_and_per_page():
    url = _trending_url(dt.date(2026, 4, 25), per_page=3)
    assert "created:>2026-04-18" in url
    assert "sort=stars" in url
    assert "order=desc" in url
    assert "per_page=3" in url


def test_client_parses_search_response():
    payload = {
        "items": [
            {
                "full_name": "smabe/TokenPal",
                "stargazers_count": 9001,
                "description": "An ASCII desktop buddy",
                "language": "Python",
                "html_url": "https://github.com/smabe/TokenPal",
            },
            {
                "full_name": "x/y",
                "stargazers_count": 100,
                "description": "Other",
                "language": "Rust",
                "html_url": "u",
            },
        ]
    }
    with patch(
        "tokenpal.senses.github_trending._client.http_json", return_value=payload,
    ):
        repos = fetch_top_repos(limit=2)

    assert [r.full_name for r in repos] == ["smabe/TokenPal", "x/y"]
    assert repos[0] == GHRepo(
        full_name="smabe/TokenPal",
        stars=9001,
        description="An ASCII desktop buddy",
        language="Python",
        url="https://github.com/smabe/TokenPal",
    )


@pytest.mark.parametrize("payload", [None, {}, {"items": []}, {"items": [{}]}, "x"])
def test_client_returns_empty_on_bad_response(payload: Any):
    with patch(
        "tokenpal.senses.github_trending._client.http_json", return_value=payload,
    ):
        assert fetch_top_repos(limit=3) == []


async def test_poll_emits_summary_with_all_repos(enabled_config: dict[str, Any]):
    repos = [
        GHRepo(
            full_name="someone/cool-thing",
            stars=1234,
            description="It does the thing",
            language="Rust",
            url="u1",
        ),
        GHRepo(
            full_name="other/thing", stars=500, description="d2", language="Go", url="u2",
        ),
    ]
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repos", return_value=repos,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == (
        "Trending GitHub (last 7d): "
        "someone/cool-thing — 1234★ (Rust) — It does the thing | "
        "other/thing — 500★ (Go) — d2"
    )


async def test_poll_omits_language_when_missing(enabled_config: dict[str, Any]):
    repos = [GHRepo(full_name="x/y", stars=10, description="d", language="", url="")]
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repos", return_value=repos,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == "Trending GitHub (last 7d): x/y — 10★ — d"


async def test_poll_truncates_long_description(enabled_config: dict[str, Any]):
    repos = [GHRepo(full_name="x/y", stars=1, description="A" * 200, language="", url="")]
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repos", return_value=repos,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "A" * 200 not in reading.summary
    assert "…" in reading.summary


async def test_poll_filters_sensitive_terms(enabled_config: dict[str, Any]):
    repos = [
        GHRepo(
            full_name="evil/1password-leak", stars=100, description="d", language="", url="",
        ),
        GHRepo(full_name="x/clean", stars=50, description="d", language="", url=""),
    ]
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repos", return_value=repos,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "1password" not in reading.summary.lower()
    assert "x/clean" in reading.summary


async def test_poll_dedups_unchanged_summary(enabled_config: dict[str, Any]):
    repos = [GHRepo(full_name="x/y", stars=1, description="d", language="", url="")]
    sense = GitHubTrendingSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.github_trending.sense.fetch_top_repos", return_value=repos,
    ):
        first = await sense.poll()
        second = await sense.poll()
    assert first is not None
    assert second is None
