"""Typing cadence sense — WPM bursts and post-burst silence.

Counts keypress timestamps over a rolling window and buckets them into
WPM tiers. Emits on bucket transitions, at the 10-minute mark of a
sustained fast-typing burst, and when a burst ends in sudden silence.

Privacy: this module only ever sees a bare "a key was pressed" pulse
from _keyboard_bus. Key values, modifiers, and text content never
enter the process beyond the pynput listener thread. A unit test
enforces this by grepping the source for forbidden attribute names.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Literal

from tokenpal.senses import _keyboard_bus
from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

Bucket = Literal["idle", "slow", "normal", "rapid", "furious"]

_WINDOW_S = 15.0            # rolling window for WPM computation
_SUSTAINED_BURST_S = 600.0  # 10 min of continuous rapid/furious → one-off reading
_POST_BURST_SILENCE_S = 8.0 # stop detection: idle this long after a burst
_FAST_WPM = 50.0            # threshold that defines a "fast" bucket
_LOW_CONF_WPM = 50.0        # below here, bucket-change readings are low-confidence

# WPM bucket thresholds. Standard: 1 word ≈ 5 keypresses.
_BUCKETS: tuple[tuple[Bucket, float], ...] = (
    ("idle", 0.0),
    ("slow", 1.0),
    ("normal", 20.0),
    ("rapid", 50.0),
    ("furious", 90.0),
)


def _bucket_for(wpm: float) -> Bucket:
    name: Bucket = _BUCKETS[0][0]
    for candidate, threshold in _BUCKETS:
        if wpm >= threshold:
            name = candidate
    return name


def _is_fast(bucket: Bucket) -> bool:
    for name, threshold in _BUCKETS:
        if name == bucket:
            return threshold >= _FAST_WPM
    return False


@register_sense
class TypingCadence(AbstractSense):
    sense_name = "typing_cadence"
    platforms = ("windows", "darwin", "linux")
    priority = 100
    poll_interval_s = 2.0
    reading_ttl_s = 60.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self._presses: deque[float] = deque()
        self._last_press: float = 0.0

        self._current_bucket: Bucket = "idle"
        self._fast_since: float = 0.0       # when current fast run began (0 = not fast)
        self._sustained_emitted: bool = False
        # post_burst_silence only fires once per burst — requires a burst to
        # have started first. Stays True until we actually enter a fast bucket.
        self._silence_emitted: bool = True

    async def setup(self) -> None:
        _keyboard_bus.subscribe(self._on_press)

    async def teardown(self) -> None:
        _keyboard_bus.unsubscribe(self._on_press)

    def _on_press(self) -> None:
        """Called on the pynput listener thread. Must be fast + thread-safe."""
        now = time.monotonic()
        with self._lock:
            self._presses.append(now)
            self._last_press = now

    def _current_wpm(self, now: float) -> float:
        cutoff = now - _WINDOW_S
        with self._lock:
            while self._presses and self._presses[0] < cutoff:
                self._presses.popleft()
            count = len(self._presses)
        return (count * (60.0 / _WINDOW_S)) / 5.0

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        now = time.monotonic()
        wpm = self._current_wpm(now)
        bucket = _bucket_for(wpm)
        prev = self._current_bucket

        # Sustained-burst reading: 10 minutes of rapid/furious without a break.
        if _is_fast(bucket):
            if self._fast_since == 0.0:
                self._fast_since = now
                self._sustained_emitted = False
                self._silence_emitted = False
            elif (
                not self._sustained_emitted
                and now - self._fast_since >= _SUSTAINED_BURST_S
            ):
                self._sustained_emitted = True
                minutes = int((now - self._fast_since) / 60)
                self._current_bucket = bucket
                return self._reading(
                    data={"event": "sustained_burst", "minutes": minutes, "bucket": bucket},
                    summary=(
                        f"User has been typing at a {bucket} pace "
                        f"for {minutes} minutes straight"
                    ),
                    confidence=0.9,
                    changed_from=prev,
                )

        # Post-burst silence: was fast, now idle + quiet for long enough.
        if (
            _is_fast(prev)
            and bucket == "idle"
            and not self._silence_emitted
            and now - self._last_press >= _POST_BURST_SILENCE_S
        ):
            self._silence_emitted = True
            self._fast_since = 0.0
            self._current_bucket = bucket
            return self._reading(
                data={"event": "post_burst_silence", "bucket": bucket},
                summary="User stopped typing mid-flow",
                confidence=0.8,
                changed_from=prev,
            )

        # Bucket transition (entering/leaving any tier).
        if bucket != prev:
            self._current_bucket = bucket
            if not _is_fast(bucket):
                self._fast_since = 0.0
            if bucket != "idle":
                self._silence_emitted = False
            return self._reading(
                data={"event": "bucket_change", "bucket": bucket, "wpm": round(wpm)},
                summary=_transition_summary(prev, bucket, wpm),
                confidence=0.5 if wpm < _LOW_CONF_WPM else 0.7,
                changed_from=prev,
            )

        return None


def _transition_summary(prev: Bucket, bucket: Bucket, wpm: float) -> str:
    if bucket == "idle":
        return "User stopped typing"
    if bucket == "furious":
        return f"User is typing furiously (~{int(wpm)} WPM)"
    if bucket == "rapid":
        return f"User picked up the pace (~{int(wpm)} WPM)"
    if prev == "idle":
        return f"User started typing ({bucket} pace)"
    return f"User is typing at a {bucket} pace"
