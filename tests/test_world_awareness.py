"""Tests for the world_awareness sense + HN Algolia client."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tokenpal.senses.world_awareness import hn_client as hn_mod
from tokenpal.senses.world_awareness.hn_client import HNStory, fetch_top_story
from tokenpal.senses.world_awareness.sense import WorldAwarenessSense

# ---------------------------------------------------------------------------
# HN client — network / parsing
# ---------------------------------------------------------------------------


def _mock_urlopen(payload: Any) -> MagicMock:
    """Return a MagicMock suitable for patching urllib.request.urlopen as a
    context manager returning an object with .read() -> JSON bytes."""
    body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    resp = MagicMock()
    resp.read.return_value = body
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    opener = MagicMock(return_value=cm)
    return opener


def test_hn_client_parses_front_page_response():
    payload = {
        "hits": [
            {
                "title": "Show HN: A neat little project",
                "points": 321,
                "url": "https://example.com/project",
                "author": "pg",
                "created_at": "2026-04-14T12:00:00Z",
            },
            {"title": "Second story", "points": 10, "url": "", "author": "x"},
        ]
    }
    with patch.object(hn_mod.urllib.request, "urlopen", _mock_urlopen(payload)):
        story = fetch_top_story()

    assert story is not None
    assert isinstance(story, HNStory)
    assert story.title == "Show HN: A neat little project"
    assert story.points == 321
    assert story.url == "https://example.com/project"
    assert story.author == "pg"
    assert story.created_at == "2026-04-14T12:00:00Z"


def test_hn_client_returns_none_on_network_failure():
    def raiser(*a: Any, **kw: Any) -> Any:
        raise OSError("network unreachable")

    with patch.object(hn_mod.urllib.request, "urlopen", side_effect=raiser):
        assert fetch_top_story() is None


def test_hn_client_returns_none_on_malformed_json():
    resp = MagicMock()
    resp.read.return_value = b"this is not json {{{"
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False

    with patch.object(hn_mod.urllib.request, "urlopen", MagicMock(return_value=cm)):
        assert fetch_top_story() is None


def test_hn_client_returns_none_on_empty_hits():
    with patch.object(hn_mod.urllib.request, "urlopen", _mock_urlopen({"hits": []})):
        assert fetch_top_story() is None


def test_hn_client_returns_none_on_missing_hits_key():
    with patch.object(hn_mod.urllib.request, "urlopen", _mock_urlopen({})):
        assert fetch_top_story() is None


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
    with patch.object(hn_mod.urllib.request, "urlopen", _mock_urlopen(payload)):
        story = fetch_top_story()

    assert story is not None
    assert story.title == 'Foo & Bar <3 "things"'
    assert "&amp;" not in story.title


# ---------------------------------------------------------------------------
# Sense behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def enabled_config() -> dict[str, Any]:
    return {"enabled": True}


async def test_setup_enabled_config_leaves_sense_enabled(enabled_config: dict[str, Any]):
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    assert sense.enabled is True


async def test_poll_emits_reading_with_expected_summary_format(enabled_config: dict[str, Any]):
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    story = HNStory(
        title="Neat thing that happened",
        points=200,
        url="https://example.com/x",
        author="u",
        created_at="2026-04-14T00:00:00Z",
    )
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=story,
    ):
        reading = await sense.poll()

    assert reading is not None
    assert reading.summary.startswith("Top HN: '")
    assert "Neat thing that happened" in reading.summary
    assert reading.summary.endswith("— 200 points")


async def test_poll_truncates_long_title_in_summary(enabled_config: dict[str, Any]):
    long_title = "A" * 200
    story = HNStory(title=long_title, points=42, url="", author="", created_at="")
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=story,
    ):
        reading = await sense.poll()

    assert reading is not None
    # Summary is prefix + truncated title + suffix; truncated title must be
    # no more than 80 chars. Pull it out of the summary between the single quotes.
    first_q = reading.summary.index("'")
    last_q = reading.summary.rindex("'")
    shown = reading.summary[first_q + 1 : last_q]
    assert len(shown) <= 80
    # Full untruncated title should NOT appear in the summary.
    assert long_title not in reading.summary


async def test_poll_puts_url_in_data_not_summary(enabled_config: dict[str, Any]):
    url = "https://example.com/secret-slug"
    story = HNStory(title="Some story", points=10, url=url, author="a", created_at="t")
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=story,
    ):
        reading = await sense.poll()

    assert reading is not None
    assert reading.data.get("url") == url
    assert url not in reading.summary


async def test_poll_filters_sensitive_terms_in_title(enabled_config: dict[str, Any]):
    # "1password" is in SENSITIVE_APPS — HN title containing it should be dropped.
    story = HNStory(
        title="1Password had a security breach",
        points=500,
        url="https://example.com",
        author="a",
        created_at="t",
    )
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=story,
    ):
        reading = await sense.poll()

    assert reading is None


async def test_poll_silent_on_fetch_failure_after_prior_success(
    enabled_config: dict[str, Any],
):
    sense = WorldAwarenessSense(enabled_config)
    await sense.setup()
    good = HNStory(title="Nice story", points=50, url="", author="", created_at="")

    # First poll: success.
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=good,
    ):
        first = await sense.poll()
    assert first is not None

    # Second poll: network failure. Sense must return None silently — no quip.
    with patch(
        "tokenpal.senses.world_awareness.sense.fetch_top_story",
        return_value=None,
    ):
        second = await sense.poll()

    assert second is None
    # And absolutely no "couldn't reach HN" style text in anything we emit.
    if second is not None:
        assert "couldn't reach" not in second.summary.lower()
        assert "hn" not in second.summary.lower() or "top hn" in second.summary.lower()


