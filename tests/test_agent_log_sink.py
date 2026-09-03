"""The last hop between the agent runner and the real UI.

Both halves of ``make_agent_log``'s persist handling were mutation-tested and
found uncovered: deleting either one left the whole suite green while desktop
content flowed into ~/.tokenpal/logs and memory.db. These pin them.
"""
from __future__ import annotations

import logging
from typing import Any

from tokenpal.app import make_agent_log

FIXTURE = "SECRET-FIXTURE-7731"


class _RecordingOverlay:
    def __init__(self) -> None:
        self.logged: list[tuple[str, bool]] = []

    def log_buddy_message(
        self, text: str, *, markup: bool = False, url: str | None = None,
        persist: bool = True,
    ) -> None:
        self.logged.append((text, persist))

    def schedule_callback(self, fn: Any) -> None:
        fn()


def test_unpersisted_line_never_reaches_the_log_file(caplog) -> None:
    overlay = _RecordingOverlay()
    agent_log = make_agent_log(overlay)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="tokenpal.app"):
        agent_log(f"the screen said {FIXTURE}", persist=False)

    assert FIXTURE not in caplog.text
    assert "unpersisted" in caplog.text


def test_unpersisted_flag_is_forwarded_to_the_overlay() -> None:
    overlay = _RecordingOverlay()
    agent_log = make_agent_log(overlay)  # type: ignore[arg-type]
    agent_log(f"the screen said {FIXTURE}", persist=False)

    assert overlay.logged == [(f"the screen said {FIXTURE}", False)]


def test_ordinary_line_is_logged_and_persisted(caplog) -> None:
    overlay = _RecordingOverlay()
    agent_log = make_agent_log(overlay)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="tokenpal.app"):
        agent_log("nothing secret here")

    assert "nothing secret here" in caplog.text
    assert overlay.logged == [("nothing secret here", True)]
