"""Brain → news_callback dispatch path."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tokenpal.brain.news_buffer import NewsItem
from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse
from tokenpal.senses.base import SenseReading


class _MockLLM(AbstractLLMBackend):
    backend_name = "mock"
    platforms = ("darwin", "linux", "windows")

    def __init__(self) -> None:
        super().__init__({"max_tokens": 40})

    async def setup(self) -> None:
        pass

    async def generate(
        self, prompt: str, max_tokens: int = 256, **_: Any,
    ) -> LLMResponse:
        return LLMResponse(text="", tokens_used=0, model_name="mock", latency_ms=0.0)

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 256,
        **_: Any,
    ) -> LLMResponse:
        return LLMResponse(text="", tokens_used=0, model_name="mock", latency_ms=0.0)

    async def teardown(self) -> None:
        pass


def _make_brain(news_callback: Any) -> Brain:
    return Brain(
        senses=[],
        llm=_MockLLM(),
        ui_callback=MagicMock(),
        personality=PersonalityEngine("You are a test bot."),
        news_callback=news_callback,
    )


def _hn_reading() -> SenseReading:
    return SenseReading(
        sense_name="world_awareness",
        timestamp=0.0,
        data={"stories": [
            {"title": "thing one", "points": 10, "url": "https://e/1"},
            {"title": "thing two", "points": 9, "url": "https://e/2"},
        ]},
        summary="…",
    )


def _weather_reading() -> SenseReading:
    return SenseReading(
        sense_name="weather",
        timestamp=0.0,
        data={"temp": 70},
        summary="sunny",
    )


def test_dispatch_forwards_new_items() -> None:
    seen: list[list[NewsItem]] = []
    brain = _make_brain(news_callback=lambda items: seen.append(list(items)))
    brain._dispatch_news([_hn_reading()])
    assert len(seen) == 1
    assert {i.title for i in seen[0]} == {"thing one", "thing two"}


def test_dispatch_dedupes_repeated_polls() -> None:
    seen: list[list[NewsItem]] = []
    brain = _make_brain(news_callback=lambda items: seen.append(list(items)))
    brain._dispatch_news([_hn_reading()])
    brain._dispatch_news([_hn_reading()])
    assert len(seen) == 1


def test_dispatch_skips_non_news_senses() -> None:
    seen: list[list[NewsItem]] = []
    brain = _make_brain(news_callback=lambda items: seen.append(list(items)))
    brain._dispatch_news([_weather_reading()])
    assert seen == []


def test_dispatch_swallows_callback_exception() -> None:
    def boom(_items: list[NewsItem]) -> None:
        raise RuntimeError("kaboom")

    brain = _make_brain(news_callback=boom)
    brain._dispatch_news([_hn_reading()])


def test_dispatch_noops_when_callback_unset() -> None:
    brain = _make_brain(news_callback=None)
    brain._dispatch_news([_hn_reading()])
