"""Tests for the lobsters sense + hottest.json client."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.senses.lobsters._client import LobstersStory, fetch_top_stories
from tokenpal.senses.lobsters.sense import LobstersSense


def test_client_parses_hottest_response():
    payload = [
        {"title": "First", "score": 87, "url": "https://example.com/x"},
        {"title": "Second", "score": 50, "url": "u2"},
        {"title": "Third", "score": 30, "url": ""},
        {"title": "Fourth", "score": 10, "url": ""},
    ]
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        stories = fetch_top_stories(limit=3)

    assert stories == [
        LobstersStory(title="First", score=87, url="https://example.com/x"),
        LobstersStory(title="Second", score=50, url="u2"),
        LobstersStory(title="Third", score=30, url=""),
    ]


@pytest.mark.parametrize("payload", [None, [], {"oops": True}, [123]])
def test_client_returns_empty_on_bad_response(payload: Any):
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        assert fetch_top_stories(limit=3) == []


def test_client_unescapes_html_entities():
    payload = [{"title": "Foo &amp; Bar &quot;baz&quot;", "score": 1, "url": ""}]
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        stories = fetch_top_stories(limit=3)
    assert stories[0].title == 'Foo & Bar "baz"'


def test_client_skips_unparsable_items():
    payload = [
        {"title": "Good", "score": 5, "url": ""},
        "not a dict",
        {"title": "", "score": 1},  # parse fails — empty title
        {"title": "Also good", "score": 2, "url": ""},
    ]
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        stories = fetch_top_stories(limit=3)
    assert [s.title for s in stories] == ["Good", "Also good"]


async def test_poll_emits_summary_with_all_headlines(enabled_config: dict[str, Any]):
    stories = [
        LobstersStory(title="First", score=42, url=""),
        LobstersStory(title="Second", score=10, url=""),
    ]
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == "Top Lobsters: 'First' — 42 pts | 'Second' — 10 pts"
    assert reading.data["stories"][0]["title"] == "First"


async def test_poll_truncates_long_title(enabled_config: dict[str, Any]):
    stories = [LobstersStory(title="A" * 200, score=1, url="")]
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "A" * 200 not in reading.summary
    assert "…" in reading.summary


async def test_poll_filters_sensitive_titles(enabled_config: dict[str, Any]):
    stories = [
        LobstersStory(title="1Password incident", score=10, url=""),
        LobstersStory(title="Clean story", score=5, url=""),
    ]
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "1Password" not in reading.summary
    assert "Clean story" in reading.summary


async def test_poll_returns_none_when_all_filtered(enabled_config: dict[str, Any]):
    stories = [LobstersStory(title="1Password breach", score=10, url="")]
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_stories", return_value=stories,
    ):
        assert await sense.poll() is None


async def test_poll_dedups_unchanged_summary(enabled_config: dict[str, Any]):
    stories = [LobstersStory(title="Same", score=5, url="")]
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_stories", return_value=stories,
    ):
        first = await sense.poll()
        second = await sense.poll()
    assert first is not None
    assert second is None
