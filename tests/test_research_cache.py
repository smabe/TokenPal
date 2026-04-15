"""Brain-level research cache integration."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.research import ResearchSession, Source
from tokenpal.brain.stop_reason import ResearchStopReason


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    m = MemoryStore(tmp_path / "m.db")
    m.setup()
    return m


class _BrainStub:
    """Minimal receiver for Brain's cache methods — they only read
    ``_memory`` and ``_research.config``."""

    def __init__(self, memory: MemoryStore, cache_ttl_s: float) -> None:
        from types import SimpleNamespace

        from tokenpal.config.schema import ResearchConfig

        self._memory = memory
        self._research = SimpleNamespace(
            config=ResearchConfig(cache_ttl_s=cache_ttl_s)
        )

    def _load_research_cache(self, question: str):
        from tokenpal.brain.orchestrator import Brain
        return Brain._load_research_cache(self, question)

    def _save_research_cache(self, question: str, session: ResearchSession) -> None:
        from tokenpal.brain.orchestrator import Brain
        Brain._save_research_cache(self, question, session)

    def _research_cache_key(self, question: str) -> str:
        from tokenpal.brain.orchestrator import Brain
        return Brain._research_cache_key(self, question)

    def _research_cache_ttl(self) -> float | None:
        from tokenpal.brain.orchestrator import Brain
        return Brain._research_cache_ttl(self)


def _make_brain(memory: MemoryStore, cache_ttl_s: float = 86400.0) -> _BrainStub:
    return _BrainStub(memory, cache_ttl_s)


def test_save_then_load_round_trip(memory: MemoryStore) -> None:
    stub = _make_brain(memory)
    session = ResearchSession(
        question="why is the sky blue",
        sources=[
            Source(number=1, url="https://x", title="T", excerpt="e", backend="ddg"),
        ],
        answer="Rayleigh scattering. [1]",
        stopped_reason=ResearchStopReason.COMPLETE,
    )

    stub._save_research_cache("why is the sky blue", session)

    hit = stub._load_research_cache("Why is the SKY Blue")
    assert hit is not None
    assert hit.stopped_reason == ResearchStopReason.COMPLETE
    assert hit.answer.startswith("(cached ")
    assert "Rayleigh" in hit.answer
    assert len(hit.sources) == 1
    assert hit.sources[0].url == "https://x"


def test_expired_cache_miss(memory: MemoryStore) -> None:
    stub = _make_brain(memory, cache_ttl_s=5.0)
    session = ResearchSession(
        question="q", answer="a", stopped_reason=ResearchStopReason.COMPLETE
    )
    stub._save_research_cache("q", session)
    assert memory._conn is not None
    memory._conn.execute(
        "UPDATE research_cache SET created_at = ? WHERE question_hash = ?",
        (time.time() - 60, stub._research_cache_key("q")),
    )
    memory._conn.commit()

    assert stub._load_research_cache("q") is None


def test_disabled_when_ttl_zero(memory: MemoryStore) -> None:
    stub = _make_brain(memory, cache_ttl_s=0.0)
    session = ResearchSession(
        question="q", answer="a", stopped_reason=ResearchStopReason.COMPLETE
    )
    stub._save_research_cache("q", session)
    assert stub._load_research_cache("q") is None
