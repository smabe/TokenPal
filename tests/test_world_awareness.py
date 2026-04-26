"""Tests for the world_awareness sense + HN Algolia client."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tokenpal.senses.world_awareness.hn_client import HNStory, fetch_top_stories
from tokenpal.senses.world_awareness.sense import WorldAwarenessSense

# ---------------------------------------------------------------------------
# HN client — network / parsing
# ---------------------------------------------------------------------------


def test_hn_client_parses_front_page_response():
    payload = {
        "hits": [
            {
                "title": "Show HN: a project",
                "points": 321,
                "url": "https://example.com/project",
                "author": "pg",
                "created_at": "2026-04-14T12:00:00Z",
            },
            {"title": "Second", "points": 10, "url": "", "author": "x"},
            {"title": "Third", "points": 5, "url": "", "author": "y"},
        ]
    }
    with patch(
        "tokenpal.senses.world_awareness.hn_client.http_json", return_value=payload,
    ):
        stories = fetch_top_stories(limit=3)

    assert [s.title for s in stories] == ["Show HN: a project", "Second", "Third"]
    assert stories[0] == HNStory(
        title="Show HN: a project",
        points=321,
        url="https://example.com/project",
        author="pg",
        created_at="2026-04-14T12:00:00Z",
    )


@pytest.mark.parametrize("payload", [None, {}, {"hits": []}, "not json"])
def test_hn_client_returns_empty_on_bad_response(payload: Any):
    with patch(
        "tokenpal.senses.world_awareness.hn_client.http_json", return_value=payload,
    ):
        assert fetch_top_stories(limit=3) == []


def test_hn_client_unescapes_html_entities_in_title():
    payload = {
        "hits": [
            {
                "title": "Foo &amp; Bar &lt;3 &quot;things&quot;",
                "points": 5,
                "url": "https://example.com",
                "author": "a",
                "created_at": "t",
            }
        ]
    }
    with patch(
        "tokenpal.senses.world_awareness.hn_client.http_json", return_value=payload,
    ):
        stories = fetch_top_stories(limit=3)

    assert stories[0].title == 'Foo & Bar <3 "things"'


# ---------------------------------------------------------------------------
# Sense behavior
# ---------------------------------------------------------------------------


async def test_setup_enabled_config_leaves_sense_enabled(enabled_config: dict[str, Any]):
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    assert sense.enabled is True


async def test_poll_emits_summary_with_all_headlines(enabled_config: dict[str, Any]):
    stories = [
        HNStory(title="A", points=200, url="u1", author="a", created_at="t"),
        HNStory(title="B", points=50, url="u2", author="b", created_at="t"),
    ]
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.summary == "Top HN: 'A' — 200 pts | 'B' — 50 pts"
    assert reading.data["stories"][0]["url"] == "u1"


async def test_poll_truncates_long_title_in_summary(enabled_config: dict[str, Any]):
    stories = [HNStory(title="A" * 200, points=42, url="", author="", created_at="")]
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "A" * 200 not in reading.summary
    assert "…" in reading.summary


async def test_poll_puts_url_in_data_not_summary(enabled_config: dict[str, Any]):
    url = "https://example.com/secret-slug"
    stories = [HNStory(title="t", points=10, url=url, author="a", created_at="t")]
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert reading.data["stories"][0]["url"] == url
    assert url not in reading.summary


async def test_poll_filters_sensitive_terms_in_title(enabled_config: dict[str, Any]):
    stories = [
        HNStory(title="1Password breach", points=500, url="", author="a", created_at="t"),
        HNStory(title="Clean", points=10, url="", author="b", created_at="t"),
    ]
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_stories", return_value=stories,
    ):
        reading = await sense.poll()
    assert reading is not None
    assert "1Password" not in reading.summary
    assert "Clean" in reading.summary


async def test_poll_silent_on_fetch_failure(enabled_config: dict[str, Any]):
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_stories", return_value=[],
    ):
        assert await sense.poll() is None
