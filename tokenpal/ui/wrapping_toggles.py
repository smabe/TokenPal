"""Checkbox / RadioButton subclasses that wrap their label across multiple lines.

Textual's stock `ToggleButton` hard-codes `get_content_height` to return 1 and
sets `text-wrap: nowrap; text-overflow: ellipsis`, so long labels in a narrow
modal get truncated with an ellipsis. These subclasses opt into wrapping by
re-asking the rendered Content how tall it would be at the available width.
"""

from __future__ import annotations

from typing import ClassVar

from textual.geometry import Size
from textual.widgets import Checkbox, RadioButton

_WRAP_CSS = """
    text-wrap: wrap;
    text-overflow: clip;
    height: auto;
    width: 1fr;
"""


class WrappingCheckbox(Checkbox):
    DEFAULT_CSS: ClassVar[str] = "WrappingCheckbox {" + _WRAP_CSS + "}"

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return max(1, self.render().get_height(self.styles, width))  # type: ignore[arg-type]


class WrappingRadioButton(RadioButton):
    DEFAULT_CSS: ClassVar[str] = "WrappingRadioButton {" + _WRAP_CSS + "}"

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return max(1, self.render().get_height(self.styles, width))  # type: ignore[arg-type]
