"""Shared audio types.

Lives outside ``tokenpal/audio/tts.py`` so the brain can label input
sources without dragging the audio output module in. ``InputSource``
collides-by-name avoidance: ``tokenpal.brain.research.Source`` is the
research-citation dataclass.
"""

from __future__ import annotations

from typing import Literal

InputSource = Literal["typed", "voice", "ambient"]
