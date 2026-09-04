"""The ``read_selection`` action: refusals, envelope size, and a full agent
run proving the read reaches no sink.

The OS read itself is covered by ``test_selected_text.py``'s fake bridge;
here ``capture_selection`` is stubbed so the tests exercise what the action
adds — consent order, the envelope it hands the model, and what the agent
runner does with it. The consent-missing case belongs to the parametrized
contract test and is not repeated.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from tests._helpers import (
    ScriptedLLM,
    agent_brain,
    assert_no_leak,
    ok_response,
    selected_text,
    tool_call,
    tool_call_response,
)
from tokenpal.actions.read_selection import _MAX_CHARS, ReadSelectionAction
from tokenpal.brain.agent import _MESSAGE_RESULT_CAP
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.personality import SENSITIVE_CONTENT_TERMS
from tokenpal.desktop.content import refuse_if_sensitive

FIXTURE = "SECRET-FIXTURE-7731 and the rest of the paragraph"
GOAL = "tell me what I selected"
PERSONA_LINE = "Peeked and stashed it in the log for you."


@pytest.fixture(autouse=True)
def _consented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tokenpal.desktop.content.has_consent", lambda *a, **k: True)


def _stub_capture(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.read_selection.capture_selection",
        lambda **kwargs: result,
    )


async def test_a_sensitive_source_app_refusal_is_returned_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = refuse_if_sensitive("Messages")
    assert refusal is not None
    _stub_capture(monkeypatch, refusal)

    result = await ReadSelectionAction({}).execute()

    assert result.success is False
    assert result.output == refusal.output
    assert "Messages" not in result.output


async def test_a_successful_read_becomes_the_envelope_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_capture(monkeypatch, selected_text(FIXTURE))

    result = await ReadSelectionAction({}).execute()

    assert result.success is True
    assert result.output.startswith('<desktop_content kind="selection" app="TextEdit">')
    assert FIXTURE in result.output
    assert result.display_text is None


async def test_the_worst_case_envelope_fits_the_runners_result_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body of the shortest sensitive term, one per line, scrubs every line
    to the 10-char ``[filtered]`` — the worst growth — and the envelope must
    still fit under the runner's cap. Derived from the live term list so a
    shorter term added later fails here instead of in the runner."""
    shortest = min(SENSITIVE_CONTENT_TERMS, key=len)
    _stub_capture(monkeypatch, selected_text((f"{shortest}\n" * 400)[:_MAX_CHARS]))

    result = await ReadSelectionAction({}).execute()

    assert len(result.output) <= _MESSAGE_RESULT_CAP
    assert result.output.endswith("</desktop_content>")


async def test_an_agent_run_delivers_the_selection_without_persisting_it(
    tmp_path: Path, caplog: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    _stub_capture(monkeypatch, selected_text(FIXTURE))
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    try:
        llm = ScriptedLLM([
            tool_call_response(tool_call("read_selection")),
            ok_response(f"You selected {FIXTURE}."),
            ok_response(PERSONA_LINE),
        ])
        brain, buf = agent_brain(llm, [ReadSelectionAction({})], memory)

        session = await brain._handle_agent_goal(GOAL)

        assert session.desktop_content is True
        envelope_chars = len(selected_text(FIXTURE).content.to_prompt_block())
        assert f"← [desktop content: {envelope_chars} chars, not shown] [unpersisted]" in buf
        assert f"You selected {FIXTURE}. [unpersisted]" in buf
        assert_no_leak(FIXTURE, lines=buf, caplog_text=caplog.text, memory=memory)
    finally:
        memory.teardown()
