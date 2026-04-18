"""Tests for chain-rule (morning_monologue) + running-bit IdleFireResult paths."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.idle_rules import IdleToolRule, rule_by_name
from tokenpal.brain.idle_tools import IdleToolRoller, build_context
from tokenpal.config.schema import IdleToolsConfig


class _StubAction(AbstractAction):
    action_name = "stub"
    description = "test stub"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False

    def __init__(
        self, output: str = "OUT", success: bool = True,
    ) -> None:
        super().__init__({})
        self._output = output
        self._success = success
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ActionResult:
        self.calls.append(kwargs)
        return ActionResult(output=self._output, success=self._success)


def _make_action(name: str, output: str = "OUT", success: bool = True) -> _StubAction:
    a = _StubAction(output=output, success=success)
    a.action_name = name  # type: ignore[misc]
    return a


def _ctx(**overrides: Any) -> Any:
    defaults: dict[str, Any] = dict(
        now=datetime(2026, 4, 20, 7, 30),  # Mon morning for the monologue window
        session_minutes=5,
        first_session_of_day=True,
        active_readings={},
        mood="snarky",
        time_since_last_comment_s=60.0,
        consent_web_fetches=True,
    )
    defaults.update(overrides)
    return build_context(**defaults)


# ---------------------------------------------------------------------------
# Chain fan-out
# ---------------------------------------------------------------------------


async def test_chain_rule_invokes_all_extra_tools() -> None:
    primary = _make_action("weather_forecast_week", output="partly cloudy week")
    sunrise = _make_action("sunrise_sunset", output="sunrise 6:45am")
    onday = _make_action("on_this_day", output="1912 Titanic sank")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={
            "weather_forecast_week": primary,
            "sunrise_sunset": sunrise,
            "on_this_day": onday,
        },
        rules=(rule_by_name("morning_monologue"),),  # type: ignore[arg-type]
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx())
    assert result is not None
    assert result.rule_name == "morning_monologue"
    assert result.tool_output == "partly cloudy week"
    assert "sunrise_sunset" in result.extra_outputs
    assert "on_this_day" in result.extra_outputs
    assert result.extra_outputs["sunrise_sunset"] == "sunrise 6:45am"


async def test_chain_gracefully_degrades_when_extra_tool_errors() -> None:
    primary = _make_action("weather_forecast_week", output="storms")
    sunrise = _make_action("sunrise_sunset", output="", success=False)
    onday = _make_action("on_this_day", output="something historic")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={
            "weather_forecast_week": primary,
            "sunrise_sunset": sunrise,
            "on_this_day": onday,
        },
        rules=(rule_by_name("morning_monologue"),),  # type: ignore[arg-type]
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx())
    assert result is not None
    assert result.tool_output == "storms"
    # Failed tool drops out; successful extras still carried.
    assert "sunrise_sunset" not in result.extra_outputs
    assert "on_this_day" in result.extra_outputs


# ---------------------------------------------------------------------------
# Running-bit wiring
# ---------------------------------------------------------------------------


async def test_running_bit_rule_populates_flags() -> None:
    action = _make_action("word_of_the_day", output="oxymoron: contradictory")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"word_of_the_day": action},
        rules=(rule_by_name("morning_word"),),  # type: ignore[arg-type]
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx(
        now=datetime(2026, 4, 20, 8, 0),
        first_session_of_day=True,
    ))
    assert result is not None
    assert result.running_bit is True
    assert result.bit_decay_s == 8 * 3600
    assert result.opener_framing  # morning_word announces
    assert "{output}" in result.framing  # template preserved for orchestrator


async def test_silent_running_bit_has_empty_opener() -> None:
    action = _make_action("joke_of_the_day", output="why did the chicken…")
    rule = rule_by_name("todays_joke_bit")
    assert rule is not None
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"joke_of_the_day": action},
        rules=(rule,),
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx(
        now=datetime(2026, 4, 20, 12, 0),
        session_minutes=20,
        time_since_last_comment_s=400.0,
    ))
    assert result is not None
    assert result.running_bit is True
    assert result.opener_framing == ""
    assert result.bit_decay_s == 4 * 3600


# ---------------------------------------------------------------------------
# Non-chain rules unaffected
# ---------------------------------------------------------------------------


async def test_non_chain_rule_has_empty_extra_outputs() -> None:
    action = _make_action("memory_query", output="some stat")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"memory_query": action},
        rules=(rule_by_name("memory_recall"),),  # type: ignore[arg-type]
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx(
        session_minutes=20,
        time_since_last_comment_s=700.0,
        consent_web_fetches=False,
    ))
    assert result is not None
    assert result.extra_outputs == {}
    assert result.running_bit is False


# ---------------------------------------------------------------------------
# Orchestrator-side: custom rule with extra_tool_names missing action should
# still succeed on the primary tool.
# ---------------------------------------------------------------------------


async def test_chain_missing_extra_action_does_not_block() -> None:
    custom = IdleToolRule(
        name="test_chain",
        tool_name="primary_tool",
        description="test",
        weight=1.0,
        cooldown_s=0.0,
        predicate=lambda _c: True,
        framing="riff",
        needs_web_fetches=False,
        extra_tool_names=("missing_tool",),
    )
    primary = _make_action("primary_tool", output="primary-ok")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"primary_tool": primary},
        rules=(custom,),
        rng=random.Random(0),
    )
    result = await roller.maybe_fire(_ctx())
    assert result is not None
    assert result.tool_output == "primary-ok"
    assert result.extra_outputs == {}
