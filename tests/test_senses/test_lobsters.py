"""Tests for the lobsters sense + hottest.json client."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.senses.lobsters._client import LobstersStory, fetch_top_story
from tokenpal.senses.lobsters.sense import LobstersSense


def test_client_parses_hottest_response():
    payload = [
        {"title": "A neat lobsters story", "score": 87, "url": "https://example.com/x"},
        {"title": "second", "score": 1},
    ]
    with patch(
        "tokenpal.senses.lobsters._client.http_json", return_value=payload,
    ):
        story = fetch_top_story()

    assert story == LobstersStory(
        title="A neat lobsters story", score=87, url="https://example.com/x",
    )


@pytest.mark.parametrize("payload", [None, [], {"oops": True}, [123]])
def test_client_returns_none_on_bad_response(payload: Any):
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        assert fetch_top_story() is None


def test_client_unescapes_html_entities():
    payload = [{"title": "Foo &amp; Bar &quot;baz&quot;", "score": 1, "url": ""}]
    with patch("tokenpal.senses.lobsters._client.http_json", return_value=payload):
        story = fetch_top_story()
    assert story is not None
    assert story.title == 'Foo & Bar "baz"'


async def test_poll_emits_reading_with_expected_summary(enabled_config: dict[str, Any]):
    story = LobstersStory(title="Cool thing", score=42, url="u")
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_story", return_value=story,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == "Top Lobsters: 'Cool thing' — 42 points"
    assert reading.data["url"] == "u"


async def test_poll_truncates_long_title(enabled_config: dict[str, Any]):
    story = LobstersStory(title="A" * 200, score=1, url="")
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_story", return_value=story,
    ):
        reading = await sense.poll()
    assert reading is not None
    first_q = reading.summary.index("'")
    last_q = reading.summary.rindex("'")
    assert len(reading.summary[first_q + 1 : last_q]) <= 80


async def test_poll_filters_sensitive_titles(enabled_config: dict[str, Any]):
    story = LobstersStory(title="1Password incident", score=10, url="")
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_story", return_value=story,
    ):
        assert await sense.poll() is None


async def test_poll_dedups_unchanged_summary(enabled_config: dict[str, Any]):
    story = LobstersStory(title="Same", score=5, url="")
    sense = LobstersSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.lobsters.sense.fetch_top_story", return_value=story,
    ):
        first = await sense.poll()
        second = await sense.poll()
    assert first is not None
    assert second is None
