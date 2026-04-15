"""Verify Brain injects scheduler/memory into focus actions at construction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tokenpal.actions.focus.logs import HydrationLogAction
from tokenpal.actions.focus.reminders import StretchReminderAction
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import Brain
from tokenpal.brain.personality import PersonalityEngine


@pytest.fixture()
def memory(tmp_path: Path) -> MemoryStore:
    m = MemoryStore(db_path=tmp_path / "brain.db")
    m.setup()
    yield m
    m.teardown()


def test_brain_injects_scheduler_and_memory(memory: MemoryStore) -> None:
    hydration = HydrationLogAction({})  # no memory at construction
    stretch = StretchReminderAction({})  # no scheduler at construction
    assert hydration._memory is None
    assert stretch._scheduler is None

    personality = PersonalityEngine(persona_prompt="test")
    brain = Brain(
        senses=[],
        llm=MagicMock(),
        ui_callback=lambda _t: None,
        personality=personality,
        memory=memory,
        actions=[hydration, stretch],
    )

    assert hydration._memory is memory
    assert stretch._scheduler is brain.proactive


async def test_brain_proactive_paused_during_conversation() -> None:
    personality = PersonalityEngine(persona_prompt="test")
    brain = Brain(
        senses=[],
        llm=MagicMock(),
        ui_callback=lambda _t: None,
        personality=personality,
    )
    # No conversation -> not paused (and no sensitive app snapshot).
    assert brain._proactive_paused() is False

    # Simulate an active conversation session.
    from tokenpal.brain.orchestrator import ConversationSession

    brain._conversation = ConversationSession()
    brain._conversation.add_user_turn("hey")
    assert brain._proactive_paused() is True
