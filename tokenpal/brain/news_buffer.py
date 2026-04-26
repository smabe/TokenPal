"""News buffer — extracts headlines from world-news sense readings,
dedupes them by URL (or source+title when URL is empty), caps at a
ring buffer. In-memory only; nothing here writes to disk.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from tokenpal.senses.base import SenseReading

NEWS_SOURCES = ("world_awareness", "lobsters", "github_trending")
_DEFAULT_MAXLEN = 200


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    url: str
    meta: str
    description: str
    timestamp: float

    @property
    def dedupe_key(self) -> str:
        return self.url if self.url else f"{self.source}:{self.title}"


def extract_news_items(reading: SenseReading) -> list[NewsItem]:
    now = time.time()
    if reading.sense_name == "world_awareness":
        return _extract_stories(reading, now, score_field="points")
    if reading.sense_name == "lobsters":
        return _extract_stories(reading, now, score_field="score")
    if reading.sense_name == "github_trending":
        return _extract_repos(reading, now)
    return []


def _extract_stories(
    reading: SenseReading, now: float, *, score_field: str,
) -> list[NewsItem]:
    raw = reading.data.get("stories")
    if not isinstance(raw, list):
        return []
    out: list[NewsItem] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        if not title:
            continue
        score = s.get(score_field)
        meta = f"{score} pts" if isinstance(score, int) else ""
        out.append(NewsItem(
            source=reading.sense_name,
            title=title,
            url=str(s.get("url", "") or "").strip(),
            meta=meta,
            description="",
            timestamp=now,
        ))
    return out


def _extract_repos(reading: SenseReading, now: float) -> list[NewsItem]:
    raw = reading.data.get("repos")
    if not isinstance(raw, list):
        return []
    out: list[NewsItem] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        name = str(r.get("full_name", "")).strip()
        if not name:
            continue
        stars = r.get("stars")
        language = str(r.get("language", "") or "").strip()
        bits: list[str] = []
        if isinstance(stars, int):
            bits.append(f"{stars}★")
        if language:
            bits.append(language)
        out.append(NewsItem(
            source="github_trending",
            title=name,
            url=str(r.get("url", "") or "").strip(),
            meta=" · ".join(bits),
            description=str(r.get("description", "") or "").strip(),
            timestamp=now,
        ))
    return out


class NewsBuffer:
    """Dedupes and caps a rolling list of NewsItems.

    Public API:
        add(items): returns the subset that wasn't already in the buffer.
        items:     read-only ordered view of everything currently held.
    """

    def __init__(self, maxlen: int = _DEFAULT_MAXLEN) -> None:
        self._items: deque[NewsItem] = deque(maxlen=maxlen)
        self._seen: set[str] = set()

    def add(self, items: list[NewsItem]) -> list[NewsItem]:
        new: list[NewsItem] = []
        for it in items:
            key = it.dedupe_key
            if key in self._seen:
                continue
            # Drop the about-to-be-evicted entry from `_seen` before the
            # deque rotates, otherwise `_seen` grows unbounded as the
            # buffer churns.
            if (
                self._items.maxlen is not None
                and len(self._items) == self._items.maxlen
            ):
                self._seen.discard(self._items[0].dedupe_key)
            self._items.append(it)
            self._seen.add(key)
            new.append(it)
        return new

    @property
    def items(self) -> list[NewsItem]:
        return list(self._items)


__all__ = [
    "NEWS_SOURCES",
    "NewsBuffer",
    "NewsItem",
    "extract_news_items",
]
