"""Tests for the M3 LLMInitiatedRoller (issue #33)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from tests._helpers import ScriptedLLM
from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.idle_rules import IdleToolContext
from tokenpal.brain.idle_tools import FireTracker
from tokenpal.brain.idle_tools_m3 import (
    M3_CATALOG,
    MEMORY_QUERY_DEFAULT_METRIC,
    LLMInitiatedRoller,
)
from tokenpal.config.schema import IdleToolsConfig
from tokenpal.llm.base import LLMResponse, ToolCall

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubAction(AbstractAction):
    """Catalog stand-in. Subclasses set action_name + canned output."""

    parameters = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False
    consent_category = ""

    async def execute(self, **kwargs: Any) -> ActionResult:
        # Echo args so tests can assert sanitization happened.
        if kwargs:
            return ActionResult(output=f"out:{kwargs}", success=True)
        return ActionResult(output=f"out:{type(self).action_name}", success=True)


def _make_action(name: str, *, web: bool = False) -> AbstractAction:
    cls = type(
        f"_Action_{name}",
        (_StubAction,),
        {
            "action_name": name,
            "description": f"stub for {name}",
            "consent_category": "web_fetches" if web else "",
        },
    )
    return cls({})


def _ctx(*, consent_web: bool = True, mood: str = "snarky") -> IdleToolContext:
    return IdleToolContext(
        now=datetime(2026, 4, 25, 14, 0),
        session_minutes=30,
        first_session_of_day=False,
        active_readings={},
        mood=mood,
        weather_summary="",
        time_since_last_comment_s=600.0,
        consent_web_fetches=consent_web,
        daily_streak_days=0,
        install_age_days=10,
        pattern_callbacks=(),
    )


def _config(*, llm_on: bool = True) -> IdleToolsConfig:
    return IdleToolsConfig(
        enabled=True,
        global_cooldown_s=180.0,
        max_per_hour=6,
        llm_initiated_enabled=llm_on,
        llm_initiated_cooldown_s=1800.0,
        llm_initiated_max_per_hour=1,
    )


def _full_actions() -> dict[str, AbstractAction]:
    """All M3-catalog actions registered, web flag set per M3_NEEDS_WEB."""
    from tokenpal.brain.idle_tools_m3 import M3_NEEDS_WEB

    return {
        name: _make_action(name, web=name in M3_NEEDS_WEB)
        for name in M3_CATALOG
    }


def _llm_response(*, tool_calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(
        text="" if tool_calls else "ok",
        tokens_used=0,
        model_name="test",
        latency_ms=0,
        tool_calls=tool_calls or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_by_default_skips_llm_call() -> None:
    llm = ScriptedLLM([])
    roller = LLMInitiatedRoller(
        config=_config(llm_on=False),
        actions=_full_actions(),
        llm=llm,
        tracker=FireTracker(),
    )
    assert await roller.maybe_fire(_ctx()) is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_llm_decline_returns_none_no_state_mutation() -> None:
    llm = ScriptedLLM([_llm_response(tool_calls=[])])
    tracker = FireTracker()
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    assert await roller.maybe_fire(_ctx()) is None
    assert tracker.last_any is None
    assert tracker.m3_last_fire is None
    assert len(llm.calls) == 1  # the picker call happened, just no tool


@pytest.mark.asyncio
async def test_llm_picks_valid_tool_returns_fire_result() -> None:
    llm = ScriptedLLM([_llm_response(tool_calls=[
        ToolCall(id="t1", name="word_of_the_day", arguments={}),
    ])])
    tracker = FireTracker()
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    fire = await roller.maybe_fire(_ctx())
    assert fire is not None
    assert fire.rule_name == "llm_initiated:word_of_the_day"
    assert fire.tool_name == "word_of_the_day"
    assert fire.success is True
    assert tracker.last_by_tool["word_of_the_day"] is not None
    assert tracker.m3_last_fire is not None
    assert len(tracker.m3_recent_fires) == 1


@pytest.mark.asyncio
async def test_out_of_catalog_tool_rejected() -> None:
    llm = ScriptedLLM([_llm_response(tool_calls=[
        ToolCall(id="t1", name="open_app", arguments={"name": "Mail"}),
    ])])
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=FireTracker(),
    )
    assert await roller.maybe_fire(_ctx()) is None


@pytest.mark.asyncio
async def test_consent_filters_web_tools_from_catalog() -> None:
    """When web consent is missing, only no-web tools should appear in spec."""
    llm = ScriptedLLM([_llm_response(tool_calls=[])])
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=FireTracker(),
    )
    await roller.maybe_fire(_ctx(consent_web=False))
    sent_tool_names = {
        spec["function"]["name"] for spec in llm.calls[0][1]
    }
    # Only the offline subset survives.
    assert sent_tool_names == {"moon_phase", "sunrise_sunset", "memory_query"}


@pytest.mark.asyncio
async def test_cross_path_per_tool_cooldown_filters_moon() -> None:
    """A deterministic-side fire of moon_phase blocks M3 from picking it."""
    import time

    llm = ScriptedLLM([_llm_response(tool_calls=[])])
    tracker = FireTracker()
    # Simulate a deterministic fire of moon_phase 1 hour ago.
    tracker.last_by_tool["moon_phase"] = time.monotonic() - 3600.0
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    await roller.maybe_fire(_ctx())
    sent_tool_names = {spec["function"]["name"] for spec in llm.calls[0][1]}
    # 24h cooloff, only 1h elapsed - moon_phase must be filtered.
    assert "moon_phase" not in sent_tool_names


@pytest.mark.asyncio
async def test_circuit_breaker_filters_after_three_consecutive_picks() -> None:
    import time

    llm = ScriptedLLM([_llm_response(tool_calls=[])])
    tracker = FireTracker()
    tracker.consecutive_same_tool["random_fact"] = 3
    # Within the 2h circuit cool-off window.
    tracker.last_by_tool["random_fact"] = time.monotonic() - 60.0
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    await roller.maybe_fire(_ctx())
    sent_tool_names = {spec["function"]["name"] for spec in llm.calls[0][1]}
    assert "random_fact" not in sent_tool_names


@pytest.mark.asyncio
async def test_m3_cooldown_blocks_back_to_back_fires() -> None:
    import time

    llm = ScriptedLLM([_llm_response(tool_calls=[])])
    tracker = FireTracker()
    # M3 fired 5 minutes ago - well within the 30-minute cooldown.
    tracker.m3_last_fire = time.monotonic() - 300.0
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    assert await roller.maybe_fire(_ctx()) is None
    # Picker LLM should NOT have been called - cooldown bailed out first.
    assert llm.calls == []


@pytest.mark.asyncio
async def test_memory_query_missing_metric_gets_sanitized_default() -> None:
    """LLM omits the required `metric` arg; sanitizer injects the default."""
    llm = ScriptedLLM([_llm_response(tool_calls=[
        ToolCall(id="t1", name="memory_query", arguments={}),
    ])])
    actions = _full_actions()
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=actions,
        llm=llm,
        tracker=FireTracker(),
    )
    fire = await roller.maybe_fire(_ctx())
    assert fire is not None
    # Stub action echoes its kwargs into output - confirm the default landed.
    assert MEMORY_QUERY_DEFAULT_METRIC in fire.tool_output


@pytest.mark.asyncio
async def test_consecutive_streak_increments_on_repeat_picks() -> None:
    llm = ScriptedLLM([
        _llm_response(tool_calls=[
            ToolCall(id="t1", name="random_fact", arguments={}),
        ]),
    ])
    tracker = FireTracker()
    roller = LLMInitiatedRoller(
        config=_config(),
        actions=_full_actions(),
        llm=llm,
        tracker=tracker,
    )
    await roller.maybe_fire(_ctx())
    assert tracker.consecutive_same_tool["random_fact"] == 1
    # All other tools should be 0 (fresh streak).
    assert tracker.consecutive_same_tool["moon_phase"] == 0
