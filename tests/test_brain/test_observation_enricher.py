"""Tests for ObservationEnricher — per-sense snapshot enrichment."""

from __future__ import annotations

from typing import Any

import pytest

from tokenpal.brain.observation_enricher import ObservationEnricher


class _StubAppEnricher:
    """AppEnricher stand-in that returns pre-seeded descriptions."""

    def __init__(self, descriptions: dict[str, str | None] | None = None) -> None:
        self._descriptions = descriptions or {}
        self.calls: list[str] = []

    async def enrich(self, name: str) -> str | None:
        self.calls.append(name)
        return self._descriptions.get(name)


class _ReadingStub:
    def __init__(self, summary: str = "", data: dict[str, Any] | None = None) -> None:
        self.summary = summary
        self.data = data or {}


def _enricher(descriptions: dict[str, str | None] | None = None) -> tuple[
    ObservationEnricher, _StubAppEnricher,
]:
    stub = _StubAppEnricher(descriptions)
    return ObservationEnricher(app_enricher=stub), stub


@pytest.mark.asyncio
async def test_app_awareness_splices_description_into_snapshot() -> None:
    enricher, stub = _enricher({"Cronometer": "nutrition tracker"})
    snapshot = "App: Cronometer | It's 10 AM"
    readings = {
        "app_awareness": _ReadingStub(
            summary="Cronometer foreground",
            data={"app_name": "Cronometer"},
        ),
    }
    result = await enricher.enrich(snapshot, readings)
    assert result == "App: Cronometer (nutrition tracker) | It's 10 AM"
    assert stub.calls == ["Cronometer"]


@pytest.mark.asyncio
async def test_app_awareness_returns_snapshot_unchanged_when_description_missing() -> None:
    """Unknown apps (network failure, gated, etc.) leave the snapshot alone."""
    enricher, _ = _enricher({"Cronometer": None})
    snapshot = "App: Cronometer | It's 10 AM"
    readings = {
        "app_awareness": _ReadingStub(data={"app_name": "Cronometer"}),
    }
    result = await enricher.enrich(snapshot, readings)
    assert result == snapshot


@pytest.mark.asyncio
async def test_app_awareness_noop_without_reading() -> None:
    enricher, stub = _enricher()
    snapshot = "App: Cronometer | It's 10 AM"
    result = await enricher.enrich(snapshot, {})
    assert result == snapshot
    assert stub.calls == []


@pytest.mark.asyncio
async def test_process_heat_appends_description() -> None:
    """A known process gets its description appended to the sense summary."""
    enricher, stub = _enricher({"Docker Desktop": "container runtime manager"})
    summary = "CPU pinned — Docker Desktop is working hard"
    snapshot = f"App: Ghostty\n{summary}"
    readings = {
        "process_heat": _ReadingStub(
            summary=summary,
            data={"top_process": "Docker Desktop"},
        ),
    }
    result = await enricher.enrich(snapshot, readings)
    assert "Docker Desktop is container runtime manager" in result


@pytest.mark.asyncio
async def test_process_heat_noop_when_description_missing() -> None:
    enricher, _ = _enricher({"Docker Desktop": None})
    summary = "CPU pinned — Docker Desktop is working hard"
    snapshot = f"App: Ghostty\n{summary}"
    readings = {
        "process_heat": _ReadingStub(
            summary=summary,
            data={"top_process": "Docker Desktop"},
        ),
    }
    assert await enricher.enrich(snapshot, readings) == snapshot


@pytest.mark.asyncio
async def test_process_heat_noop_when_summary_missing_from_snapshot() -> None:
    """Defensive: reading's summary might have been stripped by a composite."""
    enricher, stub = _enricher({"Docker Desktop": "container runtime"})
    snapshot = "App: Ghostty"  # summary absent
    readings = {
        "process_heat": _ReadingStub(
            summary="CPU pinned — Docker Desktop is working hard",
            data={"top_process": "Docker Desktop"},
        ),
    }
    result = await enricher.enrich(snapshot, readings)
    assert result == snapshot
    # We still asked the enricher — that's fine; the bail is below it.
    assert stub.calls == []


@pytest.mark.asyncio
async def test_chained_enrichment_both_apply() -> None:
    """App-awareness splice must land even when process_heat also fires."""
    enricher, _ = _enricher({
        "Cronometer": "nutrition tracker",
        "Docker Desktop": "container runtime",
    })
    snapshot = "App: Cronometer | Docker Desktop is working hard"
    readings = {
        "app_awareness": _ReadingStub(data={"app_name": "Cronometer"}),
        "process_heat": _ReadingStub(
            summary="Docker Desktop is working hard",
            data={"top_process": "Docker Desktop"},
        ),
    }
    result = await enricher.enrich(snapshot, readings)
    assert "App: Cronometer (nutrition tracker)" in result
    assert "Docker Desktop is container runtime" in result
