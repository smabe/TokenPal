"""Integration tests for Brain._handle_followup and submit_followup_question."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tokenpal.actions.base import ActionResult
from tokenpal.brain.orchestrator import Brain, BrainMode


class _ScriptedAction:
    """Stand-in for ResearchFollowupAction — returns a preset ActionResult."""

    action_name = "research_followup"

    def __init__(self, result: ActionResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> ActionResult:
        self.calls.append(kwargs)
        return self._result


def _bare_brain_with_action(action: _ScriptedAction) -> Brain:
    brain = Brain.__new__(Brain)
    brain._actions = {"research_followup": action}
    brain._mode = BrainMode.IDLE
    brain._status_callback = None
    brain._agent = type("A", (), {"log_callback": None})()
    captured: list[str] = []
    brain._ui_callback = captured.append
    brain._ui_capture = captured  # for tests to read
    brain._last_comment_time = 0.0
    brain._push_status = lambda: None  # type: ignore[method-assign]
    return brain


@pytest.mark.asyncio
async def test_followup_handler_unwraps_answer_xml() -> None:
    tool_result_xml = (
        "<tool_result tool=\"research_followup\" status=\"complete\">\n"
        "<answer>\nfollow-up answer body\n</answer>\n"
        "<sources>\n[1] https://x.com - title\n</sources>\n"
        "<telemetry>\nfollowup=1/5 cache_read=2500 output_tokens=42\n</telemetry>\n"
        "</tool_result>"
    )
    action = _ScriptedAction(ActionResult(output=tool_result_xml, success=True))
    brain = _bare_brain_with_action(action)
    await brain._handle_followup("what else?")
    assert len(brain._ui_capture) == 1
    # UI should see the answer body WITHOUT the XML wrapper
    assert brain._ui_capture[0] == "follow-up answer body"
    # Action received the question
    assert action.calls == [{"question": "what else?"}]


@pytest.mark.asyncio
async def test_followup_handler_shows_error_verbatim_on_failure() -> None:
    action = _ScriptedAction(ActionResult(
        output="research_followup: no recent cloud research in session.",
        success=False,
    ))
    brain = _bare_brain_with_action(action)
    await brain._handle_followup("whatever")
    # Error path: show the failure output as-is (not XML-unwrapped)
    assert brain._ui_capture[0].startswith("research_followup: no recent")


@pytest.mark.asyncio
async def test_followup_handler_when_action_not_registered() -> None:
    brain = Brain.__new__(Brain)
    brain._actions = {}  # research_followup not loaded
    brain._mode = BrainMode.IDLE
    brain._status_callback = None
    brain._agent = type("A", (), {"log_callback": None})()
    captured: list[str] = []
    brain._ui_callback = captured.append
    brain._last_comment_time = 0.0
    brain._push_status = lambda: None  # type: ignore[method-assign]

    await brain._handle_followup("q")
    assert any("not registered" in msg for msg in captured)


@pytest.mark.asyncio
async def test_followup_handler_restores_idle_mode_on_success() -> None:
    action = _ScriptedAction(ActionResult(
        output="<tool_result><answer>ok</answer></tool_result>", success=True,
    ))
    brain = _bare_brain_with_action(action)
    await brain._handle_followup("q")
    assert brain._mode is BrainMode.IDLE


@pytest.mark.asyncio
async def test_followup_handler_restores_idle_mode_on_crash() -> None:
    class _CrashAction(_ScriptedAction):
        async def execute(self, **_kw: Any) -> ActionResult:
            raise RuntimeError("boom")

    brain = _bare_brain_with_action(
        _CrashAction(ActionResult(output="", success=False)),
    )
    await brain._handle_followup("q")
    assert brain._mode is BrainMode.IDLE
    assert any("crashed" in msg for msg in brain._ui_capture)


def test_submit_followup_question_posts_to_queue() -> None:
    """Verify the submit method enqueues via _post_threadsafe."""
    brain = Brain.__new__(Brain)
    posts: list[tuple[Any, str, str]] = []

    def fake_post(queue: Any, item: str, label: str) -> None:
        posts.append((queue, item, label))

    brain._post_threadsafe = fake_post  # type: ignore[method-assign]
    brain._followup_queue = asyncio.Queue()

    brain.submit_followup_question("what about X?")
    assert len(posts) == 1
    assert posts[0][1] == "what about X?"
    assert posts[0][2] == "research followup"
    assert posts[0][0] is brain._followup_queue
