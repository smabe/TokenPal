"""One tool for arming, cancelling and listing recurring nudges.

`Schedule` (tokenpal/brain/schedule.py) owns parsing and validation; this
module surfaces its ValueError messages verbatim, because they name the
arguments this tool exposes.

Opt-in: `resolve_actions` never instantiates this unless "reminder" is in
`[tools] enabled_tools`, and `Brain` only hydrates armed rows while it is
enabled, so un-ticking it stops the nudges without destroying them.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.brain.personality import contains_sensitive_content_term
from tokenpal.brain.proactive import ProactiveScheduler
from tokenpal.brain.schedule import MAX_INTERVAL_MIN, MIN_INTERVAL_MIN, Schedule

log = logging.getLogger(__name__)

# The schema's enum and execute's validation read this same tuple, so a mode
# the model can discover and a mode that works cannot drift apart.
ACTION_MODES: tuple[str, ...] = ("arm", "cancel", "list")

_NO_BRAIN = "Reminders need a running brain; not available here."
_ONE_FORM = (
    "arm needs exactly one of every_min (a repeating interval) or "
    "at (a daily time like '22:30')."
)
_NEED_LABEL = "label is required for arm -- what should I say when it fires?"
# Never echo what tripped the filter into the reply: it is read aloud and
# lands in the persisted chat log. (The caller's DEBUG line logs raw tool
# arguments for every tool, so the log file is not covered by this.)
_SENSITIVE = "I won't store that reminder. Rephrase it without the sensitive detail."
_NEED_ID = "cancel needs the id of the reminder to remove; run list to see them."
# Each arm mints a new id, and every row survives restarts and is cancelled
# one at a time, so the armed set needs an explicit bound.
MAX_ARMED = 20
_TOO_MANY = (
    f"You already have {MAX_ARMED} reminders armed -- cancel one before "
    "arming another. Run list to see them."
)

_SLUG_RE = re.compile(r"[^\w]+", re.UNICODE)
_MAX_SLUG_LEN = 40
_WHITESPACE_RE = re.compile(r"\s+")
# The label is echoed into the tool result, the bubble and every list line.
MAX_LABEL_LEN = 200
_LABEL_TOO_LONG = f"label must be {MAX_LABEL_LEN} characters or fewer."
# id is the primary key, and is echoed in the reply and in every list line.
MAX_ID_LEN = 60
_ID_TOO_LONG = f"id must be {MAX_ID_LEN} characters or fewer."
_UNSTORABLE = "I couldn't store that reminder -- try plainer text in the label."


def _slug(label: str) -> str:
    """A short, stable id derived from the label the user typed.

    Keeps word characters in any script, so a non-Latin label yields a
    meaningful id rather than collapsing to "reminder".
    """
    slug = _SLUG_RE.sub("-", label.lower()).strip("-")[:_MAX_SLUG_LEN]
    return slug.strip("-") or "reminder"


def _unique_id(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`... so arming "stretch" twice arms two."""
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _schedule_words(schedule: Schedule) -> str:
    """Human phrasing of a schedule. Lives here, not on the value type."""
    if schedule.kind == "interval":
        assert schedule.interval_s is not None
        return f"every {int(schedule.interval_s) // 60} min"
    assert schedule.at_hour is not None and schedule.at_minute is not None
    return f"daily at {schedule.at_hour:02d}:{schedule.at_minute:02d}"


def _when(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s).strftime("%Y-%m-%d %H:%M")


def _text_arg(kwargs: dict[str, Any], name: str) -> str:
    """One line of text. Interior newlines are collapsed: `list` renders one
    reminder per line, so a label carrying one would fabricate a row."""
    value = kwargs.get(name)
    return "" if value is None else _WHITESPACE_RE.sub(" ", str(value)).strip()


@register_action
class ReminderAction(AbstractAction):
    """Arm / cancel / list the recurring nudges the ProactiveScheduler fires."""

    action_name = "reminder"
    description = (
        "Arm, cancel or list recurring nudges. Schedules are either "
        "every N minutes or a daily time like 22:30."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(ACTION_MODES),
                "description": "arm a nudge, cancel one by id, or list what is armed.",
            },
            "label": {
                "type": "string",
                "description": "What to say when it fires. Required for arm.",
            },
            "every_min": {
                "type": "integer",
                "description": (
                    f"Minutes between fires ({MIN_INTERVAL_MIN}-{MAX_INTERVAL_MIN}). "
                    "Use this or at, never both."
                ),
            },
            "at": {
                "type": "string",
                "description": (
                    "Daily fire time, 24-hour HH:MM (e.g. '22:30'). "
                    "Use this or every_min, never both."
                ),
            },
            "id": {
                "type": "string",
                "description": (
                    "Which reminder to cancel. On arm, replaces the reminder "
                    "that already has this id."
                ),
            },
        },
        "required": ["action"],
    }
    safe: ClassVar[bool] = False
    requires_confirm: ClassVar[bool] = False
    cacheable: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        # _inject_brain_deps fills this by sniffing for an attribute that
        # exists and is None; without the declaration it silently skips us.
        self._scheduler: ProactiveScheduler | None = None

    async def execute(self, **kwargs: Any) -> ActionResult:
        if self._scheduler is None:
            return ActionResult(output=_NO_BRAIN, success=False)

        mode = _text_arg(kwargs, "action").lower()
        if mode not in ACTION_MODES:
            return ActionResult(
                output=f"action must be one of: {', '.join(ACTION_MODES)}.",
                success=False,
            )

        reminder_id = _text_arg(kwargs, "id")
        # The id lands in the same persisted row as the label and in the same
        # refusal text, so it gets the same filter and the same bounds.
        if reminder_id and contains_sensitive_content_term(reminder_id):
            return ActionResult(output=_SENSITIVE, success=False)
        if len(reminder_id) > MAX_ID_LEN:
            return ActionResult(output=_ID_TOO_LONG, success=False)

        if mode == "arm":
            return self._arm(kwargs, reminder_id)
        if mode == "cancel":
            return self._cancel(reminder_id)
        return self._list()

    # ------------------------------------------------------------------

    def _arm(self, kwargs: dict[str, Any], reminder_id: str) -> ActionResult:
        assert self._scheduler is not None
        every_min = kwargs.get("every_min")
        at = kwargs.get("at")
        has_every = every_min is not None and str(every_min).strip() != ""
        has_at = at is not None and str(at).strip() != ""
        if has_every == has_at:
            return ActionResult(output=_ONE_FORM, success=False)

        label = _text_arg(kwargs, "label")
        if not label:
            return ActionResult(output=_NEED_LABEL, success=False)
        if len(label) > MAX_LABEL_LEN:
            return ActionResult(output=_LABEL_TOO_LONG, success=False)
        if contains_sensitive_content_term(label):
            return ActionResult(output=_SENSITIVE, success=False)

        try:
            schedule = (
                Schedule.interval_from_minutes(every_min)
                if has_every
                else Schedule.daily_from_hhmm(at)
            )
        except ValueError as e:
            return ActionResult(output=str(e), success=False)

        armed = self._scheduler.armed()
        replacing = bool(reminder_id) and any(n.id == reminder_id for n in armed)
        if not replacing and len(armed) >= MAX_ARMED:
            return ActionResult(output=_TOO_MANY, success=False)
        if not reminder_id:
            reminder_id = _unique_id(_slug(label), {n.id for n in armed})

        due = schedule.next_due_at(time.time())
        try:
            self._scheduler.register(
                id=reminder_id, label=label, schedule=schedule, next_due_at=due
            )
        except ValueError as e:
            # A lone surrogate (JSON "\ud800") passes every check above and
            # raises UnicodeEncodeError -- a ValueError -- when sqlite binds
            # it. register() writes _nudges before persisting, so roll the
            # in-memory nudge back or it stays armed with no row behind it.
            self._scheduler.cancel(reminder_id)
            log.warning("Reminder '%s' could not be stored: %s", reminder_id, e)
            return ActionResult(output=_UNSTORABLE, success=False)

        verb = "Replaced" if replacing else "Armed"
        return ActionResult(
            output=(
                f"{verb} '{reminder_id}': {label} -- {_schedule_words(schedule)}, "
                f"next {_when(due)}."
            )
        )

    def _cancel(self, reminder_id: str) -> ActionResult:
        assert self._scheduler is not None
        if not reminder_id:
            return ActionResult(output=_NEED_ID, success=False)
        if self._scheduler.cancel(reminder_id):
            return ActionResult(output=f"Cancelled '{reminder_id}'.")
        # cancel() answers False for an unknown id AND for a memory store that
        # is off or closed, so this must not claim the reminder never existed.
        return ActionResult(output=f"Nothing armed under '{reminder_id}'.")

    def _list(self) -> ActionResult:
        assert self._scheduler is not None
        armed = self._scheduler.armed()
        if not armed:
            return ActionResult(output="Nothing armed.")
        lines = [
            f"{n.id}  {n.label}  {_schedule_words(n.schedule)}  "
            f"next {_when(n.next_due_at)}"
            for n in armed
        ]
        return ActionResult(output="\n".join(lines))
