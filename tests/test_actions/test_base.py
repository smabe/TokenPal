"""Tests for AbstractAction and ActionResult."""

from __future__ import annotations

from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult


class _TestAction(AbstractAction):
    action_name = "test_action"
    description = "Does a test thing."
    parameters = {
        "type": "object",
        "properties": {
            "msg": {"type": "string", "description": "A message"},
        },
        "required": ["msg"],
    }

    async def execute(self, **kwargs: Any) -> ActionResult:
        return ActionResult(output=f"got: {kwargs.get('msg', '')}")


def test_to_tool_spec_format():
    action = _TestAction({})
    spec = action.to_tool_spec()

    assert spec["type"] == "function"
    assert spec["function"]["name"] == "test_action"
    assert spec["function"]["description"] == "Does a test thing."
    assert "msg" in spec["function"]["parameters"]["properties"]


async def test_execute_returns_action_result():
    action = _TestAction({})
    result = await action.execute(msg="hello")
    assert result.output == "got: hello"
    assert result.success is True


def test_action_result_defaults():
    r = ActionResult(output="done")
    assert r.success is True

    r2 = ActionResult(output="fail", success=False)
    assert r2.success is False


async def test_teardown_is_noop_by_default():
    action = _TestAction({})
    await action.teardown()  # should not raise
