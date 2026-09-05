"""Tests for the nudge emission funnel (`Brain._emit_nudge`).

The funnel shares `_emit_comment`'s output steps — bubble plus ambient TTS —
and none of its rate accounting. An armed reminder is a promise the user made
to themselves: it fires through a forced-silence window, it repeats itself
verbatim, and a short label is not swallowed by `filter_response`'s
15-character minimum.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from tokenpal.brain.orchestrator import Brain, BrainMode
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.schedule import Schedule


class _StubContext:
    def __init__(self) -> None:
        self.ack_calls = 0

    def acknowledge(self) -> None:
        self.ack_calls += 1


class _StubPersonality:
    """Records what reached the ambient-voice bookkeeping and the filter."""

    def __init__(self, filtered: str | None = None) -> None:
        self.recorded: list[str] = []
        self.filtered_inputs: list[str] = []
        self._filtered = filtered

    def record_comment(self, text: str) -> None:
        self.recorded.append(text)

    def filter_response(self, text: str) -> str | None:
        self.filtered_inputs.append(text)
        return self._filtered


@dataclass
class _Harness:
    """A bare Brain plus the sinks the funnel writes into."""

    brain: Brain
    personality: _StubPersonality
    context: _StubContext
    bubbles: list[str] = field(default_factory=list)
    spoken: list[tuple[str, str]] = field(default_factory=list)

    def scheduler(self) -> ProactiveScheduler:
        """A scheduler sharing only the ui_callback with `Brain.__init__`.

        The pause gate and the store are stubbed out. That the real Brain
        wires this callback at all is pinned separately, by
        `test_brain_wires_the_scheduler_to_the_nudge_funnel` -- without it,
        reverting that one line passes the whole suite.
        """
        return ProactiveScheduler(
            ui_callback=self.brain._emit_nudge, is_paused=lambda: False
        )


def _make_harness(*, audio: bool = False, filtered: str | None = None) -> _Harness:
    """Bare orchestrator carrying only the state the funnel touches."""
    obj = Brain.__new__(Brain)
    personality = _StubPersonality(filtered)
    context = _StubContext()
    harness = _Harness(brain=obj, personality=personality, context=context)

    obj._ui_callback = harness.bubbles.append
    obj._audio_pipeline = object() if audio else None  # type: ignore[assignment]
    obj._personality = personality  # type: ignore[assignment]
    obj._context = context  # type: ignore[assignment]
    obj._recent_outputs = deque(maxlen=10)
    obj._consecutive_comments = 0
    obj._suppressed_streak = 0
    obj._comment_timestamps = []
    obj._forced_silence_until = 0.0
    obj._last_comment_time = 0.0
    obj._paused = False
    obj._conversation = None
    obj._mode = BrainMode.IDLE

    def _speak(text: str, *, source: str) -> None:
        harness.spoken.append((text, source))

    obj._speak_async = _speak  # type: ignore[assignment,method-assign]
    return harness


def _arm_due(sched: ProactiveScheduler, label: str, id: str = "stretch") -> None:
    """Arm a reminder whose deadline has already passed."""
    sched.register(
        id=id,
        label=label,
        schedule=Schedule.interval_from_minutes(30),
        next_due_at=time.time() - 1.0,
    )


# ----------------------------------------------------------------------
# Bubble and ambient TTS
# ----------------------------------------------------------------------


def test_fired_nudge_is_spoken_when_audio_is_enabled() -> None:
    h = _make_harness(audio=True)
    sched = h.scheduler()
    _arm_due(sched, "time to stretch your legs")

    assert len(sched.tick()) == 1
    assert h.bubbles == ["time to stretch your legs"]
    assert h.spoken == [("time to stretch your legs", "ambient")]


def test_fired_nudge_does_not_raise_without_audio() -> None:
    """Called directly, not through tick(): tick swallows every ui_callback
    exception, so a raise there would show up only as an empty `spoken`."""
    h = _make_harness(audio=False)

    h.brain._emit_nudge("time to stretch your legs")

    assert h.bubbles == ["time to stretch your legs"]
    assert h.spoken == []


# ----------------------------------------------------------------------
# Exemption from the ambient gate
# ----------------------------------------------------------------------


def test_nudge_fires_during_forced_silence() -> None:
    h = _make_harness()
    h.brain._forced_silence_until = time.monotonic() + 600.0
    assert h.brain._should_comment() is False

    sched = h.scheduler()
    _arm_due(sched, "drink some water")

    assert len(sched.tick()) == 1
    assert h.bubbles == ["drink some water"]
    # The gate is untouched by the fire — still refusing an ambient comment.
    assert h.brain._should_comment() is False


# ----------------------------------------------------------------------
# No rate accounting, no dedupe ring, no acknowledge, no catchphrase state
# ----------------------------------------------------------------------


def test_nudge_touches_no_comment_bookkeeping() -> None:
    h = _make_harness(audio=True)
    h.brain._last_comment_time = 123.0
    h.brain._consecutive_comments = 2
    h.brain._suppressed_streak = 3
    h.brain._comment_timestamps = [1.0, 2.0]

    h.brain._emit_nudge("drink some water")

    assert h.brain._last_comment_time == 123.0
    assert h.brain._consecutive_comments == 2
    assert h.brain._suppressed_streak == 3
    assert h.brain._comment_timestamps == [1.0, 2.0]
    assert len(h.brain._recent_outputs) == 0
    assert h.context.ack_calls == 0
    assert h.personality.recorded == []


# ----------------------------------------------------------------------
# Repetition is the feature
# ----------------------------------------------------------------------


def test_same_reminder_fires_twice_with_identical_text() -> None:
    h = _make_harness(audio=True)
    sched = h.scheduler()
    _arm_due(sched, "time to stretch your legs")

    assert len(sched.tick()) == 1
    _arm_due(sched, "time to stretch your legs")
    # tick() spaces consecutive fires by _MIN_NUDGE_GAP_S; step past it.
    assert len(sched.tick(now=time.time() + 60.0)) == 1

    assert h.bubbles == ["time to stretch your legs"] * 2
    assert len(h.spoken) == 2
    assert len(h.brain._recent_outputs) == 0


# ----------------------------------------------------------------------
# Short labels survive
# ----------------------------------------------------------------------


def test_short_label_is_emitted_verbatim() -> None:
    """`filter_response` drops anything under 15 chars; a label never sees it."""
    h = _make_harness(audio=True, filtered=None)
    sched = h.scheduler()
    _arm_due(sched, "stand up")

    assert len(sched.tick()) == 1
    assert h.bubbles == ["stand up"]
    assert h.spoken == [("stand up", "ambient")]
    assert h.personality.filtered_inputs == []


# ----------------------------------------------------------------------
# Generated text is filtered; the label is the fallback
# ----------------------------------------------------------------------


def test_generated_text_is_filtered() -> None:
    h = _make_harness(audio=True, filtered="Legs. Use them. Up you get.")
    h.brain._emit_nudge("stand up", generated="  legs. use them. up you get.  ")

    assert h.bubbles == ["Legs. Use them. Up you get."]
    assert h.personality.filtered_inputs == ["  legs. use them. up you get.  "]


def test_dropped_generated_text_falls_back_to_the_label() -> None:
    """`filter_response` returns None for silence; the user still armed this."""
    h = _make_harness(audio=True, filtered=None)
    h.brain._emit_nudge("stand up", generated="ich bin ein drifted model")

    assert h.bubbles == ["stand up"]
    assert h.spoken == [("stand up", "ambient")]


def test_brain_wires_the_scheduler_to_the_nudge_funnel() -> None:
    """The phase's only production change is one line in `Brain.__init__`.

    Every other test builds its own scheduler, so reverting that line to
    `ui_callback=self._ui_callback` leaves the whole suite green while every
    nudge silently loses its TTS -- the gap this phase exists to close.
    """
    brain = Brain(
        senses=[],
        llm=MagicMock(),
        ui_callback=lambda _t: None,
        personality=PersonalityEngine(persona_prompt="test"),
    )

    assert brain.proactive._ui_callback == brain._emit_nudge
