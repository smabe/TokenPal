"""Tests for the IdleToolRoller — filtering, cooldowns, rate cap, picks."""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any, ClassVar

import pytest

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.brain.idle_rules import IdleToolRule, rule_by_name
from tokenpal.brain.idle_tools import IdleToolRoller, build_context
from tokenpal.config.schema import IdleToolsConfig


class _StubAction(AbstractAction):
    """Returns a canned string; records calls for assertions."""

    action_name = "stub"
    description = "test stub"
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    safe = True
    requires_confirm = False

    def __init__(self, output: str = "STUB OUTPUT") -> None:
        super().__init__({})
        self._output = output
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ActionResult:
        self.calls.append(kwargs)
        return ActionResult(output=self._output, success=True)


def _make_action(name: str, output: str = "OUT") -> _StubAction:
    action = _StubAction(output=output)
    # action_name is a ClassVar on AbstractAction; roller looks it up as an
    # instance attribute first, so shadowing is fine for this test seam.
    action.action_name = name  # type: ignore[misc]
    return action


def _ctx(**overrides: Any) -> Any:
    defaults: dict[str, Any] = dict(
        now=datetime(2026, 4, 17, 22, 30),   # evening
        session_minutes=30,
        first_session_of_day=False,
        active_readings={},
        mood="snarky",
        time_since_last_comment_s=60.0,
        consent_web_fetches=True,
    )
    defaults.update(overrides)
    return build_context(**defaults)


def _rule(
    name: str = "fixture",
    tool_name: str = "stub",
    **overrides: Any,
) -> IdleToolRule:
    defaults: dict[str, Any] = dict(
        name=name,
        tool_name=tool_name,
        description="fixture rule",
        weight=1.0,
        cooldown_s=3600.0,
        predicate=lambda _ctx: True,
        framing="riff",
        needs_web_fetches=False,
    )
    defaults.update(overrides)
    return IdleToolRule(**defaults)


# ---------------------------------------------------------------------------
# gating
# ---------------------------------------------------------------------------


async def test_disabled_config_short_circuits() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(enabled=False),
        actions={"stub": action},
        rules=(_rule(),),
    )
    assert await roller.maybe_fire(_ctx()) is None
    assert action.calls == []


async def test_consent_gate_excludes_network_rules() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"stub": action},
        rules=(_rule(needs_web_fetches=True),),
    )
    assert await roller.maybe_fire(_ctx(consent_web_fetches=False)) is None


async def test_predicate_failure_excludes_rule() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"stub": action},
        rules=(_rule(predicate=lambda _ctx: False),),
    )
    assert await roller.maybe_fire(_ctx()) is None


async def test_predicate_exception_does_not_poison_roll() -> None:
    def _boom(_ctx: Any) -> bool:
        raise RuntimeError("boom")

    good = _make_action("stub_ok", output="OK")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"stub_bad": _make_action("stub_bad"), "stub_ok": good},
        rules=(
            _rule(name="bad", tool_name="stub_bad", predicate=_boom),
            _rule(name="good", tool_name="stub_ok"),
        ),
    )
    result = await roller.maybe_fire(_ctx())
    assert result is not None
    assert result.rule_name == "good"


# ---------------------------------------------------------------------------
# cooldown + rate cap
# ---------------------------------------------------------------------------


async def test_global_cooldown_blocks_second_fire() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=600.0),
        actions={"stub": action},
        rules=(_rule(cooldown_s=0.0),),
    )
    first = await roller.maybe_fire(_ctx())
    assert first is not None
    second = await roller.maybe_fire(_ctx())
    assert second is None


async def test_per_rule_cooldown_excludes_recently_fired() -> None:
    a = _make_action("stub_a", output="A")
    b = _make_action("stub_b", output="B")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"stub_a": a, "stub_b": b},
        rules=(
            _rule(name="ra", tool_name="stub_a", cooldown_s=3600.0),
            _rule(name="rb", tool_name="stub_b", cooldown_s=0.0),
        ),
        rng=random.Random(0),
    )
    # Force rule "ra" as the first fire by giving it a big weight.
    roller._rules = (
        _rule(name="ra", tool_name="stub_a", cooldown_s=3600.0, weight=1000),
        _rule(name="rb", tool_name="stub_b", cooldown_s=0.0, weight=0.01),
    )
    first = await roller.maybe_fire(_ctx())
    assert first is not None and first.rule_name == "ra"
    second = await roller.maybe_fire(_ctx())
    assert second is not None and second.rule_name == "rb"


async def test_max_per_hour_rate_cap() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0, max_per_hour=2),
        actions={"stub": action},
        rules=(_rule(cooldown_s=0.0),),
    )
    assert await roller.maybe_fire(_ctx()) is not None
    assert await roller.maybe_fire(_ctx()) is not None
    capped = await roller.maybe_fire(_ctx())
    assert capped is None


# ---------------------------------------------------------------------------
# picking
# ---------------------------------------------------------------------------


async def test_weighted_pick_honors_weights() -> None:
    a = _make_action("stub_a", output="A")
    b = _make_action("stub_b", output="B")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"stub_a": a, "stub_b": b},
        rules=(
            _rule(name="heavy", tool_name="stub_a", weight=100.0, cooldown_s=0.0),
            _rule(name="light", tool_name="stub_b", weight=0.001, cooldown_s=0.0),
        ),
        rng=random.Random(42),
    )
    # Heavy weight should dominate across many single-shot runs.
    picks: list[str] = []
    for _ in range(20):
        r = roller._weighted_pick(list(roller._rules))
        picks.append(r.name)
    assert picks.count("heavy") >= 18


async def test_force_fire_bypasses_predicate_and_cooldown() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=99999.0),
        actions={"stub": action},
        rules=(_rule(predicate=lambda _c: False, cooldown_s=99999.0),),
    )
    result = await roller.force_fire("fixture", _ctx())
    assert result is not None
    assert action.calls == [{}]


async def test_force_fire_unknown_rule_returns_none() -> None:
    roller = IdleToolRoller(
        config=IdleToolsConfig(),
        actions={},
        rules=(),
    )
    assert await roller.force_fire("nope", _ctx()) is None


# ---------------------------------------------------------------------------
# memory_recall offline path
# ---------------------------------------------------------------------------


async def test_memory_recall_passes_metric_arg() -> None:
    action = _make_action("memory_query", output="some stat")
    rule = rule_by_name("memory_recall")
    assert rule is not None
    roller = IdleToolRoller(
        config=IdleToolsConfig(global_cooldown_s=0.0),
        actions={"memory_query": action},
        rules=(rule,),
        rng=random.Random(0),
    )
    # Predicate requires settled session.
    result = await roller.maybe_fire(_ctx(
        session_minutes=20,
        time_since_last_comment_s=700.0,
        consent_web_fetches=False,
    ))
    assert result is not None
    assert result.rule_name == "memory_recall"
    assert len(action.calls) == 1
    assert "metric" in action.calls[0]


# ---------------------------------------------------------------------------
# rule_status introspection
# ---------------------------------------------------------------------------


def test_rule_status_flags_cooldown_reason() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(),
        actions={"stub": action},
        rules=(_rule(cooldown_s=3600.0),),
    )
    roller.tracker.last_by_rule["fixture"] = time.monotonic()
    rows = roller.rule_status(_ctx())
    assert len(rows) == 1
    _, _, reason = rows[0]
    assert "cooldown" in reason


def test_rule_status_flags_consent_reason() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(),
        actions={"stub": action},
        rules=(_rule(needs_web_fetches=True),),
    )
    rows = roller.rule_status(_ctx(consent_web_fetches=False))
    _, _, reason = rows[0]
    assert "consent" in reason


def test_rule_status_reports_empty_reason_when_eligible() -> None:
    action = _make_action("stub")
    roller = IdleToolRoller(
        config=IdleToolsConfig(),
        actions={"stub": action},
        rules=(_rule(cooldown_s=0.0),),
    )
    rows = roller.rule_status(_ctx())
    _, enabled, reason = rows[0]
    assert enabled is True
    assert reason == ""


# pytest recognizes async fns because of asyncio_mode=auto (conftest).
pytest  # silence unused-import lint
