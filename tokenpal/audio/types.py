"""Shared audio types.

``InputSource`` is named explicitly to avoid colliding with
``tokenpal.brain.research.Source`` (a research-citation dataclass).
"""

from __future__ import annotations

from typing import Literal

InputSource = Literal["typed", "voice", "ambient"]
