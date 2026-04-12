"""macOS music detection — AppleScript queries to Music.app and Spotify."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from tokenpal.senses.base import AbstractSense, SenseReading
from tokenpal.senses.registry import register_sense

log = logging.getLogger(__name__)

# Check if a player is running WITHOUT launching it
_CHECK_RUNNING = 'tell application "System Events" to (name of processes) contains "{app}"'

# Get current track info (only call when player is confirmed running)
_GET_TRACK = """\
tell application "{app}"
    if player state is playing then
        set trackName to name of current track
        set artistName to artist of current track
        return artistName & " — " & trackName
    else
        return "NOT_PLAYING"
    end if
end tell"""

_PLAYERS = ("Music", "Spotify")


def _osascript(script: str, timeout: float = 3.0) -> str | None:
    """Run an AppleScript and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


@register_sense
class MacOSMusic(AbstractSense):
    sense_name = "music"
    platforms = ("darwin",)
    priority = 100
    poll_interval_s = 15.0

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._prev_track: str = ""

    async def setup(self) -> None:
        try:
            import Quartz  # noqa: F401
        except ImportError:
            log.warning("pyobjc not installed — disabling music sense")
            self.disable()

    async def poll(self) -> SenseReading | None:
        if not self.enabled:
            return None

        for player in _PLAYERS:
            # P0: Check if player is running BEFORE querying it
            running = _osascript(_CHECK_RUNNING.format(app=player))
            if running != "true":
                continue

            track_info = _osascript(_GET_TRACK.format(app=player))
            if not track_info or track_info == "NOT_PLAYING":
                continue

            # Track changed?
            changed_from = ""
            if track_info != self._prev_track and self._prev_track:
                changed_from = f"was playing {self._prev_track}"
            self._prev_track = track_info

            # Redact track from log, keep full data for LLM
            log.debug("Music: %s playing (track redacted)", player)

            return self._reading(
                data={
                    "player": player,
                    "track_info": track_info,
                    "state": "playing",
                },
                summary=f'Listening to {track_info} on {player}',
                changed_from=changed_from,
            )

        # Nothing playing — clear state, return None
        if self._prev_track:
            self._prev_track = ""
        return None

    async def teardown(self) -> None:
        pass
