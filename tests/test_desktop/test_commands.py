"""The /proofread and /explain slash handlers: parse, enqueue, say nothing."""

from __future__ import annotations

from typing import cast

from tokenpal.app import make_desktop_task_command
from tokenpal.brain.orchestrator import Brain
from tokenpal.commands import CommandDispatcher
from tokenpal.desktop.tasks import DesktopTask


class _RecordingBrain:
    def __init__(self) -> None:
        self.calls: list[tuple[DesktopTask, str | None]] = []

    def submit_desktop_task(self, task: DesktopTask, text: str | None) -> None:
        self.calls.append((task, text))


def test_inline_text_is_stripped_and_the_result_carries_no_message() -> None:
    brain = _RecordingBrain()
    result = make_desktop_task_command(cast(Brain, brain), "proofread")("  hello  ")
    assert brain.calls == [("proofread", "hello")]
    assert result.message == ""


def test_bare_command_enqueues_none() -> None:
    brain = _RecordingBrain()
    make_desktop_task_command(cast(Brain, brain), "proofread")("")
    assert brain.calls == [("proofread", None)]


def test_dispatch_routes_the_task_name_and_args() -> None:
    brain = _RecordingBrain()
    dispatcher = CommandDispatcher()
    for task in ("proofread", "explain"):
        dispatcher.register(
            task, make_desktop_task_command(cast(Brain, brain), cast(DesktopTask, task)),
        )

    dispatcher.dispatch("/explain foo")
    assert brain.calls == [("explain", "foo")]
