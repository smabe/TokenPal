"""Tests for action discovery, registration, and resolution."""

from __future__ import annotations

from typing import Any

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import (
    _ACTION_REGISTRY,
    discover_actions,
    register_action,
    resolve_actions,
)

# Ensure builtins are discovered once for the module
discover_actions()


class _DummyAction(AbstractAction):
    action_name = "dummy"
    description = "A test action."
    parameters = {"type": "object", "properties": {}}
    platforms = ("darwin", "linux", "windows")

    async def execute(self, **kwargs: Any) -> ActionResult:
        return ActionResult(output="ok")


def test_register_action_adds_to_registry():
    _ACTION_REGISTRY.pop("dummy", None)
    register_action(_DummyAction)
    assert "dummy" in _ACTION_REGISTRY
    assert _ACTION_REGISTRY["dummy"] is _DummyAction
    _ACTION_REGISTRY.pop("dummy")


def test_discover_actions_finds_builtins():
    assert "timer" in _ACTION_REGISTRY
    assert "system_info" in _ACTION_REGISTRY
    assert "open_app" in _ACTION_REGISTRY


def test_resolve_actions_respects_enabled_flags():
    actions = resolve_actions(enabled={"timer": True, "system_info": False, "open_app": True})
    names = {a.action_name for a in actions}
    assert "timer" in names
    assert "open_app" in names
    assert "system_info" not in names


def test_resolve_actions_all_disabled():
    actions = resolve_actions(enabled={"timer": False, "system_info": False, "open_app": False})
    assert actions == []
