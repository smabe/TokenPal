"""Read the text the user selected in the app they came from (macOS).

The first ``reads_desktop_content`` tool. Its output is prompt-only: see
``docs/claude/actions.md`` for the contract and ``tokenpal/desktop/content.py``
for the call order this follows.
"""

from __future__ import annotations

from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.desktop.content import require_consent
from tokenpal.desktop.selected_text import capture_selection

# The agent runner truncates a tool result at _MESSAGE_RESULT_CAP and a longer
# envelope loses its closing tag; scrubbing can nearly double a body of short
# sensitive lines. tests/test_desktop/test_read_selection.py pins the worst case.
_MAX_CHARS = 1_000


@register_action
class ReadSelectionAction(AbstractAction):
    action_name = "read_selection"
    description = (
        "Read the text the user selected in the app they were using before "
        "TokenPal (macOS). Returns at most 1,000 characters; with nothing "
        "selected it returns the start of the focused field instead. "
        "Needs 'desktop content' consent."
    )
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    platforms = ("darwin",)
    safe = True
    requires_confirm = False
    cacheable: ClassVar[bool] = False
    reads_desktop_content: ClassVar[bool] = True

    async def execute(self, **kwargs: Any) -> ActionResult:
        refusal = require_consent()
        if refusal is not None:
            return refusal
        captured = capture_selection(max_chars=_MAX_CHARS)
        if isinstance(captured, ActionResult):
            return captured
        return ActionResult(output=captured.content.to_prompt_block())
