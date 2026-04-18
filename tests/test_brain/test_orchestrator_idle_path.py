"""Integration-ish tests for the Brain's idle-tool emission path."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from tokenpal.brain.idle_tools import IdleFireResult
from tokenpal.brain.orchestrator import Brain
from tokenpal.config.schema import IdleToolsConfig


def _bare_brain() -> Brain:
    """Construct just enough Brain state to test the idle gate + telemetry."""
    obj = Brain.__new__(Brain)
    obj._idle_tools_config = IdleToolsConfig(enabled=True)
    obj._paused = False
    obj._conversation = None
    obj._forced_silence_until = 0.0
    obj._memory = None
    # _any_long_task inspects _mode; default idle state is fine.
    from tokenpal.brain.orchestrator import BrainMode
    obj._mode = BrainMode.IDLE
    return obj


def test_idle_tools_eligible_when_config_on() -> None:
    brain = _bare_brain()
    assert brain._idle_tools_eligible()


def test_idle_tools_blocked_when_disabled() -> None:
    brain = _bare_brain()
    brain._idle_tools_config = IdleToolsConfig(enabled=False)
    assert not brain._idle_tools_eligible()


def test_idle_tools_blocked_when_paused() -> None:
    brain = _bare_brain()
    brain._paused = True
    assert not brain._idle_tools_eligible()


def test_idle_tools_blocked_during_forced_silence() -> None:
    brain = _bare_brain()
    brain._forced_silence_until = time.monotonic() + 60.0
    assert not brain._idle_tools_eligible()


def test_record_idle_fire_noop_without_memory() -> None:
    brain = _bare_brain()
    fire = IdleFireResult(
        rule_name="x", tool_name="y", tool_output="z",
        framing="f", latency_ms=1.0, success=True,
    )
    # No crash when memory is None.
    brain._record_idle_fire(fire, emitted=True)


class _RecordingMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.enabled = True

    def record_observation(
        self, sense_name: str, event_type: str, summary: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.calls.append({
            "sense_name": sense_name,
            "event_type": event_type,
            "summary": summary,
            "data": data,
        })


def test_record_idle_fire_writes_telemetry_row() -> None:
    brain = _bare_brain()
    brain._memory = _RecordingMemory()
    fire = IdleFireResult(
        rule_name="morning_word", tool_name="word_of_the_day",
        tool_output="oxymoron: contradictory words",
        framing="announce it",
        latency_ms=42.0, success=True,
    )
    brain._record_idle_fire(fire, emitted=True)
    assert len(brain._memory.calls) == 1
    row = brain._memory.calls[0]
    assert row["sense_name"] == "idle_tools"
    assert row["event_type"] == "idle_tool_fire"
    assert row["summary"] == "morning_word"
    assert row["data"]["tool"] == "word_of_the_day"
    assert row["data"]["emitted"] is True
    assert row["data"]["tool_success"] is True
    assert row["data"]["latency_ms"] == 42


async def test_generate_comment_returns_false_on_sensitive_app() -> None:
    """False return is what lets the brain loop cede the tick to idle rolls."""
    brain = _bare_brain()
    brain._context = type("C", (), {"snapshot": lambda self: "banking app"})()
    brain._personality = type("P", (), {
        "check_sensitive_app": lambda self, s: True,
    })()
    assert await brain._generate_comment() is False


async def test_generate_comment_returns_true_on_easter_egg() -> None:
    """Easter eggs count as emitted, so no redundant idle roll on the tick."""
    brain = _bare_brain()
    brain._context = type("C", (), {"snapshot": lambda self: "noon"})()
    brain._personality = type("P", (), {
        "check_sensitive_app": lambda self, s: False,
        "check_easter_egg": lambda self, s: "Lunchtime.",
    })()
    emitted: list[str] = []
    brain._emit_comment = lambda text, acknowledge=False: emitted.append(text)
    assert await brain._generate_comment() is True
    assert emitted == ["Lunchtime."]


def test_build_idle_context_wires_session_minutes(monkeypatch: Any) -> None:
    from tokenpal.brain.context import ContextWindowBuilder

    brain = _bare_brain()
    brain._personality = type("P", (), {"mood": "snarky"})()
    brain._context = ContextWindowBuilder(max_tokens=256)
    brain._session_started_at = time.monotonic() - 600
    brain._first_session_of_day = True
    brain._last_comment_time = time.monotonic() - 30
    monkeypatch.setattr(
        "tokenpal.brain.orchestrator.datetime",
        type("D", (), {"now": staticmethod(
            lambda: datetime(2026, 4, 17, 9, 30)
        )})(),
    )
    monkeypatch.setattr(
        "tokenpal.brain.orchestrator.has_consent", lambda _: False,
    )
    ctx = brain._build_idle_context()
    assert ctx.session_minutes >= 9
    assert ctx.first_session_of_day is True
    assert ctx.consent_web_fetches is False
    assert ctx.mood == "snarky"
