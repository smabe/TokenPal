"""News buffer extraction + dedupe."""

from __future__ import annotations

from tokenpal.brain.news_buffer import NewsBuffer, NewsItem, extract_news_items
from tokenpal.senses.base import SenseReading


def _hn_reading() -> SenseReading:
    return SenseReading(
        sense_name="world_awareness",
        timestamp=1.0,
        data={
            "stories": [
                {
                    "title": "Show HN: a thing",
                    "points": 342,
                    "url": "https://example.com/a",
                    "author": "ada",
                    "created_at": "2026-04-26",
                },
                {
                    "title": "Self-post with no url",
                    "points": 12,
                    "url": "",
                    "author": "bob",
                    "created_at": "2026-04-26",
                },
            ],
        },
        summary="…",
    )


def _lobsters_reading() -> SenseReading:
    return SenseReading(
        sense_name="lobsters",
        timestamp=2.0,
        data={
            "stories": [
                {
                    "title": "Rust 2.0 released",
                    "score": 88,
                    "url": "https://lobste.rs/s/abc",
                },
            ],
        },
        summary="…",
    )


def _github_reading() -> SenseReading:
    return SenseReading(
        sense_name="github_trending",
        timestamp=3.0,
        data={
            "repos": [
                {
                    "full_name": "foo/bar",
                    "stars": 12345,
                    "description": "A thing that does things",
                    "language": "Python",
                    "url": "https://github.com/foo/bar",
                },
                {
                    "full_name": "no/lang",
                    "stars": 9,
                    "description": "",
                    "language": "",
                    "url": "https://github.com/no/lang",
                },
            ],
        },
        summary="…",
    )


def test_extracts_hn_stories() -> None:
    items = extract_news_items(_hn_reading())
    assert len(items) == 2
    first = items[0]
    assert isinstance(first, NewsItem)
    assert first.source == "world_awareness"
    assert first.title == "Show HN: a thing"
    assert first.url == "https://example.com/a"
    assert "342" in first.meta


def test_extracts_lobsters_stories() -> None:
    items = extract_news_items(_lobsters_reading())
    assert len(items) == 1
    assert items[0].source == "lobsters"
    assert items[0].title == "Rust 2.0 released"
    assert items[0].url == "https://lobste.rs/s/abc"
    assert "88" in items[0].meta


def test_extracts_github_repos() -> None:
    items = extract_news_items(_github_reading())
    assert len(items) == 2
    assert items[0].source == "github_trending"
    assert items[0].title == "foo/bar"
    assert items[0].url == "https://github.com/foo/bar"
    assert items[0].meta == "12345★ · Python"
    assert items[0].description == "A thing that does things"
    assert items[1].meta == "9★"
    assert items[1].description == ""


def test_unknown_sense_returns_empty() -> None:
    reading = SenseReading(
        sense_name="weather",
        timestamp=0.0,
        data={"foo": "bar"},
        summary="sunny",
    )
    assert extract_news_items(reading) == []


def test_buffer_dedupes_by_url() -> None:
    buf = NewsBuffer()
    items = extract_news_items(_hn_reading())
    new_first = buf.add(items)
    assert len(new_first) == 2
    new_second = buf.add(items)
    assert new_second == []
    assert len(buf.items) == 2


def test_buffer_dedupe_key_falls_back_to_source_title_when_url_empty() -> None:
    """HN self-posts can land with empty url. Dedupe must still work
    via (source, title) so two distinct self-posts don't collapse."""
    buf = NewsBuffer()
    a = NewsItem(source="world_awareness", title="A", url="", meta="", description="", timestamp=0.0)
    b = NewsItem(source="world_awareness", title="B", url="", meta="", description="", timestamp=0.0)
    a_dup = NewsItem(source="world_awareness", title="A", url="", meta="", description="", timestamp=1.0)
    new = buf.add([a, b, a_dup])
    assert len(new) == 2
    assert {i.title for i in new} == {"A", "B"}


def test_buffer_caps_at_maxlen() -> None:
    buf = NewsBuffer(maxlen=3)
    items = [
        NewsItem(
            source="lobsters", title=f"t{i}", url=f"u{i}",
            meta="", description="", timestamp=float(i),
        )
        for i in range(5)
    ]
    buf.add(items)
    assert len(buf.items) == 3
    titles = [i.title for i in buf.items]
    assert titles == ["t2", "t3", "t4"]
