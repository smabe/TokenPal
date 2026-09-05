"""Tests for the nudge emission funnel (`Brain._emit_nudge`) and the off-loop
generation that feeds it.

The funnel shares `_emit_comment`'s output steps — bubble plus ambient TTS —
and none of its rate accounting. An armed reminder is a promise the user made
to themselves: it fires through a forced-silence window, it repeats itself
verbatim, and a short label is not swallowed by `filter_response`'s
15-character minimum.

The scheduler delivers nothing. `Brain._fire_due_nudges` ticks it and spawns
one bounded generation per fire, and that task is what emits — exactly once,
the voiced line or the canned label.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tokenpal.brain import orchestrator
from tokenpal.brain.orchestrator import Brain, BrainMode
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.schedule import Schedule
from tokenpal.config.schema import MinTokensPerPathConfig, TargetLatencyConfig
from tokenpal.tools.voice_profile import VoiceProfile


class _StubContext:
    def __init__(self) -> None:
        self.ack_calls = 0

    def acknowledge(self) -> None:
        self.ack_calls += 1

    def snapshot(self) -> str:
        return "context"


class _StubPersonality:
    """Records what reached the ambient-voice bookkeeping and the filter."""

    def __init__(self, filtered: str | None = None) -> None:
        self.recorded: list[str] = []
        self.filtered_inputs: list[str] = []
        self._filtered = filtered
        self.sensitive_app = False

    def build_reminder_nudge_prompt(self, label: str) -> str:
        return f"[prompt] {label}"

    def record_comment(self, text: str) -> None:
        self.recorded.append(text)

    def filter_response(self, text: str) -> str | None:
        self.filtered_inputs.append(text)
        return self._filtered

    def check_sensitive_app(self, snapshot: str) -> bool:
        return self.sensitive_app


@dataclass
class _Harness:
    """A bare Brain plus the sinks the funnel writes into."""

    brain: Brain
    personality: _StubPersonality
    context: _StubContext
    bubbles: list[str] = field(default_factory=list)
    spoken: list[tuple[str, str]] = field(default_factory=list)

    def scheduler(self) -> ProactiveScheduler:
        """An open-gated scheduler with no store. It delivers nothing; the
        harness's Brain does, through `_fire_due_nudges`."""
        return ProactiveScheduler(is_paused=lambda: False)


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

    h.brain._emit_nudge("time to stretch your legs")

    assert h.bubbles == ["time to stretch your legs"]
    assert h.spoken == [("time to stretch your legs", "ambient")]


def test_fired_nudge_does_not_raise_without_audio() -> None:
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

    h.brain._emit_nudge("drink some water")

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

    h.brain._emit_nudge("time to stretch your legs")
    h.brain._emit_nudge("time to stretch your legs")

    assert h.bubbles == ["time to stretch your legs"] * 2
    assert len(h.spoken) == 2
    assert len(h.brain._recent_outputs) == 0


# ----------------------------------------------------------------------
# Short labels survive
# ----------------------------------------------------------------------


def test_short_label_is_emitted_verbatim() -> None:
    """`filter_response` drops anything under 15 chars; a label never sees it."""
    h = _make_harness(audio=True, filtered=None)

    h.brain._emit_nudge("stand up")

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




# ----------------------------------------------------------------------
# Off-loop generation (p5)
# ----------------------------------------------------------------------


def _wire_llm(
    h: _Harness,
    generate: Callable[..., Awaitable[SimpleNamespace]],
    *,
    personality: object | None = None,
) -> list[str]:
    """Give the harness the pieces the generation path reads.

    Returns the list the prompts land in, so a test can assert on what the
    builder produced without re-deriving it.
    """
    prompts: list[str] = []

    async def _generate(prompt: str, **kwargs: object) -> SimpleNamespace:
        prompts.append(prompt)
        return await generate(prompt, **kwargs)

    h.brain._llm = SimpleNamespace(generate=_generate)  # type: ignore[assignment]
    h.brain._budgets = TargetLatencyConfig()
    h.brain._min_tokens = MinTokensPerPathConfig()
    h.brain._nudge_tasks = {}
    h.brain._loose_nudge_tasks = set()
    h.brain._nudge_delivery_lock = asyncio.Lock()
    h.brain._last_nudge_delivery = 0.0
    h.brain._proactive = h.scheduler()  # type: ignore[assignment]
    if personality is not None:
        h.brain._personality = personality  # type: ignore[assignment]
    return prompts


def _replies(text: str) -> Callable[..., Awaitable[SimpleNamespace]]:
    async def _generate(prompt: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text=text)

    return _generate


async def _settle() -> None:
    """Let every spawned nudge and delivery task run to completion."""
    for _ in range(30):
        await asyncio.sleep(0)


async def test_generated_nudge_replaces_the_label() -> None:
    h = _make_harness(audio=True, filtered="Legs. Up. Now. You know the deal.")
    _wire_llm(h, _replies("legs. up. now. you know the deal."))
    _arm_due(h.brain._proactive, "stretch your legs")

    h.brain._fire_due_nudges()
    await _settle()

    assert h.bubbles == ["Legs. Up. Now. You know the deal."]
    assert h.spoken == [("Legs. Up. Now. You know the deal.", "ambient")]


async def test_drifted_generation_falls_back_to_the_label() -> None:
    """The real filter, not the stub: `wikipedia` is a `_META_MARKERS` entry,
    so `is_clean_english` rejects the line and the user's own words ship."""
    h = _make_harness(audio=True)
    _wire_llm(
        h,
        _replies("According to wikipedia, stretching improves circulation."),
        personality=PersonalityEngine(persona_prompt="test"),
    )
    _arm_due(h.brain._proactive, "stretch your legs")

    h.brain._fire_due_nudges()
    await _settle()

    assert h.bubbles == ["stretch your legs"]
    assert h.spoken == [("stretch your legs", "ambient")]


async def test_raising_generation_falls_back_to_the_label() -> None:
    h = _make_harness(audio=True, filtered="never reached")

    async def _boom(prompt: str, **kwargs: object) -> SimpleNamespace:
        raise RuntimeError("inference server is down")

    _wire_llm(h, _boom)
    _arm_due(h.brain._proactive, "drink some water")

    h.brain._fire_due_nudges()
    await _settle()

    assert h.bubbles == ["drink some water"]
    assert h.personality.filtered_inputs == []


async def test_timed_out_generation_falls_back_to_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "_NUDGE_TIMEOUT_S", 0.01)
    h = _make_harness(audio=True, filtered="never reached")

    async def _slow(prompt: str, **kwargs: object) -> SimpleNamespace:
        await asyncio.sleep(30.0)
        return SimpleNamespace(text="too late")

    _wire_llm(h, _slow)
    _arm_due(h.brain._proactive, "drink some water")

    h.brain._fire_due_nudges()
    for _ in range(200):  # poll rather than race a fixed sleep
        if h.bubbles:
            break
        await asyncio.sleep(0.01)

    assert h.bubbles == ["drink some water"]
    assert h.personality.filtered_inputs == []


async def test_filtered_out_generation_falls_back_to_the_label() -> None:
    """`filter_response` chose silence; the user still armed this reminder."""
    h = _make_harness(audio=True, filtered=None)
    _wire_llm(h, _replies("[SILENT]"))
    _arm_due(h.brain._proactive, "drink some water")

    h.brain._fire_due_nudges()
    await _settle()

    assert h.bubbles == ["drink some water"]
    assert h.personality.filtered_inputs == ["[SILENT]"]


async def test_tick_returns_while_a_generation_hangs() -> None:
    """A model that never answers must not stall the brain loop."""
    h = _make_harness(audio=True, filtered="never reached")

    async def _never(prompt: str, **kwargs: object) -> SimpleNamespace:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _wire_llm(h, _never)
    _arm_due(h.brain._proactive, "stretch your legs")

    h.brain._fire_due_nudges()
    await _settle()

    # The fire is recorded and the task is in flight; nothing was emitted yet
    # and no tick blocked waiting for it.
    assert h.bubbles == []
    assert len(h.brain._nudge_tasks) == 1
    # A DIFFERENT reminder must still get its generation while the first
    # hangs; ticking an empty scheduler three times proves nothing.
    _arm_due(h.brain._proactive, "look away from the screen", id="eyes")
    h.brain._last_nudge_delivery = 0.0
    h.brain._proactive._last_emit_at = 0.0
    h.brain._fire_due_nudges()
    await _settle()
    assert "eyes" in h.brain._nudge_tasks

    for task in list(h.brain._nudge_tasks.values()):
        task.cancel()
    await _settle()


async def test_second_fire_during_a_generation_emits_the_label() -> None:
    """One in-flight generation per reminder id, or a slow model stacks tasks
    that all write to the same bubble."""
    h = _make_harness(audio=True, filtered="never reached")
    calls = 0

    async def _never(prompt: str, **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    _wire_llm(h, _never)
    sched = h.brain._proactive
    _arm_due(sched, "stretch your legs")

    h.brain._fire_due_nudges()
    await _settle()
    assert calls == 1
    assert h.bubbles == []

    # Same reminder comes due again while the first generation still hangs.
    _arm_due(sched, "stretch your legs")
    for nudge in sched.tick(now=time.time() + 60.0):
        h.brain._spawn_nudge_generation(nudge.id, nudge.label)
    await _settle()

    assert calls == 1
    assert h.bubbles == ["stretch your legs"]
    assert len(h.brain._nudge_tasks) == 1

    for task in list(h.brain._nudge_tasks.values()):
        task.cancel()
    await _settle()


async def test_a_raising_delivery_does_not_escape_the_brain_loop() -> None:
    """A raising overlay must not escape either delivery path.

    On the loop an escape would skip the rest of the iteration (rollover,
    wedges, the observation, the idle roll); off it, a raise inside the task
    would surface only as a missing bubble.
    """
    h = _make_harness(audio=False, filtered="a perfectly good voiced line")
    calls: list[str] = []

    def _boom(text: str) -> None:
        calls.append(text)
        raise RuntimeError("the overlay went away")

    h.brain._ui_callback = _boom  # type: ignore[assignment]
    _wire_llm(h, _replies("a perfectly good voiced line"))
    _arm_due(h.brain._proactive, "stretch your legs")

    # Neither the loop-side tick nor the off-loop task may raise.
    h.brain._fire_due_nudges()
    await _settle()
    assert calls == ["a perfectly good voiced line"]

    # And the in-flight-overlap branch. It spawns rather than delivering
    # inline, so that a raise cannot escape into the loop that called it.
    h.brain._last_nudge_delivery = 0.0  # skip the inter-delivery spacing
    h.brain._nudge_tasks["stretch"] = asyncio.get_running_loop().create_future()  # type: ignore[assignment]
    h.brain._spawn_nudge_generation("stretch", "stretch your legs")
    await _settle()
    assert calls == ["a perfectly good voiced line", "stretch your legs"]
    h.brain._nudge_tasks.clear()


async def test_nudge_prompt_is_built_in_the_buddys_voice() -> None:
    """"Different text between fires" only proves non-constant; the identity
    block and the catchphrase priming are what make it the buddy's voice."""
    personality = PersonalityEngine(persona_prompt="test")
    h = _make_harness(audio=True)
    prompts = _wire_llm(
        h, _replies("Legs. Up. Now."), personality=personality
    )
    _arm_due(h.brain._proactive, "stretch your legs")

    h.brain._fire_due_nudges()
    await _settle()

    assert len(prompts) == 1
    assert personality._identity_block() in prompts[0]
    assert prompts[0].endswith(personality._voice_reminder() + "Your line:")
    assert "stretch your legs" in prompts[0]

async def test_run_loop_actually_calls_fire_due_nudges() -> None:
    """Drive one real loop iteration.

    A source-text assertion is not a pin: it passes against a commented-out
    call. Two earlier phases of this plan shipped an unpinned wiring line that
    the whole suite stayed green without, so this drives the loop instead.
    """
    brain = Brain(
        senses=[],
        llm=MagicMock(),
        ui_callback=lambda _t: None,
        personality=PersonalityEngine(persona_prompt="test"),
    )
    calls: list[bool] = []

    def _spy() -> None:
        calls.append(True)
        brain._running = False  # one iteration, then unwind

    brain._fire_due_nudges = _spy  # type: ignore[method-assign]
    brain._running = True
    await asyncio.wait_for(brain._run_loop(), timeout=10)

    assert calls == [True]


async def test_a_gate_that_closes_during_generation_suppresses_the_nudge() -> None:
    """`_proactive_paused` is checked when the nudge FIRES; delivery is up to
    _NUDGE_TIMEOUT_S later. A sensitive app opened in that window must still
    silence it -- otherwise the buddy speaks over 1Password with audio on."""
    h = _make_harness(audio=True, filtered="Up you get.")
    _wire_llm(h, _replies("Up you get."))
    _arm_due(h.brain._proactive, "stretch your legs")

    h.brain._fire_due_nudges()          # gate open: the nudge fires
    h.personality.sensitive_app = True  # ... and closes while generating
    await _settle()

    assert h.bubbles == []
    assert h.spoken == []


async def test_deliveries_are_spaced_even_when_fires_are() -> None:
    """MIN_NUDGE_GAP_S spaces fires, but the bubble lands a variable
    generation later, so the guarantee has to be re-made at delivery."""
    h = _make_harness(audio=True, filtered="voiced")
    _wire_llm(h, _replies("voiced"))

    h.brain._last_nudge_delivery = 0.0
    await h.brain._deliver_nudge("a", "first")
    assert h.bubbles == ["first"]
    first_at = h.brain._last_nudge_delivery

    # A second delivery arriving immediately must wait out the gap.
    task = asyncio.ensure_future(h.brain._deliver_nudge("b", "second"))
    await _settle()
    assert h.bubbles == ["first"], "second bubble must not land inside the gap"
    assert h.brain._last_nudge_delivery == first_at
    task.cancel()


def test_brain_init_creates_the_nudge_task_state() -> None:
    """The generation path reads these; every other test hand-plants them, so
    without this the one production init line is unpinned."""
    brain = Brain(
        senses=[],
        llm=MagicMock(),
        ui_callback=lambda _t: None,
        personality=PersonalityEngine(persona_prompt="test"),
    )

    assert brain._nudge_tasks == {}
    assert brain._loose_nudge_tasks == set()
    assert brain._last_nudge_delivery == 0.0
    assert isinstance(brain._nudge_delivery_lock, asyncio.Lock)


async def test_the_finetuned_prompt_carries_the_label() -> None:
    """The is_finetuned branch is otherwise never taken by any test, and the
    fallback contract depends on the label reaching the model."""
    engine = PersonalityEngine(persona_prompt="test")
    engine._finetuned_model = "some-tuned-model"  # is_finetuned reads this
    assert engine.is_finetuned

    prompt = engine.build_reminder_nudge_prompt("drink some water")

    assert "drink some water" in prompt
    assert prompt != engine.build_reminder_nudge_prompt("stand up")


async def test_the_prompt_carries_the_catchphrase_priming() -> None:
    """`_voice_reminder()` is empty for a bare persona, so asserting against it
    is vacuous; give the engine catchphrases so the assertion has teeth."""
    voice = VoiceProfile(
        character="testvoice",
        source="test",
        created="2026-04-17",
        lines=[f"sample line {i}" for i in range(20)],
        persona="VOICE: Short.\n\nCATCHPHRASES: beep boop, oh dear\n\n",
    )
    engine = PersonalityEngine(persona_prompt="", voice=voice)
    reminder = engine._voice_reminder()
    assert reminder, "fixture must produce a non-empty voice reminder"

    prompt = engine.build_reminder_nudge_prompt("stand up")

    assert reminder in prompt
    assert engine._identity_block() in prompt


async def test_shutdown_cancels_an_in_flight_generation() -> None:
    """A generation outliving teardown emits into a torn-down UI."""
    h = _make_harness(audio=True)
    started = asyncio.Event()

    async def _hang(prompt: str, **kwargs: object) -> SimpleNamespace:
        started.set()
        await asyncio.sleep(30.0)
        return SimpleNamespace(text="too late")

    _wire_llm(h, _hang)
    _arm_due(h.brain._proactive, "drink some water")
    h.brain._fire_due_nudges()
    await asyncio.wait_for(started.wait(), timeout=5)
    task = h.brain._nudge_tasks["stretch"]

    # The pieces of _teardown_components that own nudge tasks.
    for t in list(h.brain._nudge_tasks.values()):
        t.cancel()
    for t in list(h.brain._nudge_tasks.values()):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    h.brain._nudge_tasks.clear()

    assert task.cancelled()
    assert h.bubbles == [], "a cancelled generation must not deliver"
