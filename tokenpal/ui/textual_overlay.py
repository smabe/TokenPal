"""Textual-based overlay — rich TUI with proper input handling."""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.errors import MarkupError
from rich.markup import escape as _esc_markup
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import MouseDown, MouseMove, MouseUp, Resize
from textual.message import Message
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Static

from tokenpal.ui.ascii_props import (
    PropSprite,
    night_star_scale,
    props_for,
    style_for,
)
from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.buddy_environment import (
    BuddyEnvironmentController,
    BuddyMotion,
    EnvironmentSnapshot,
    EnvState,
)
from tokenpal.ui.confirm_modal import ConfirmModal
from tokenpal.ui.registry import register_overlay
from tokenpal.ui.selection_modal import SelectionGroup, SelectionModal

log = logging.getLogger(__name__)

_CSS_PATH = Path(__file__).parent / "textual_overlay.tcss"
_BUDDY_PANEL_PADDING = 4
_CHAT_LOG_MIN_SPACE = 30
_CHAT_LOG_MIN_WIDTH = 25
_CHAT_LOG_DEFAULT_WIDTH = 40
_SPEECH_SCROLL_PADDING = 4
_MIN_BORDERED_REGION_WIDTH = 36
_BUBBLE_HOLD_MIN_S = 2.5
_BUBBLE_HOLD_PER_CHAR_S = 0.05


# --- Messages (all thread-safe via post_message) ---


class ShowSpeech(Message):
    def __init__(self, bubble: SpeechBubble) -> None:
        self.bubble = bubble
        super().__init__()


class HideSpeech(Message):
    pass


class ShowBuddy(Message):
    def __init__(self, frame: BuddyFrame) -> None:
        self.frame = frame
        super().__init__()


class UpdateStatus(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class LogBuddyMessage(Message):
    def __init__(self, text: str, *, markup: bool = False, url: str | None = None) -> None:
        self.text = text
        self.markup = markup
        self.url = url
        super().__init__()


class LogUserMessage(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ClearLog(Message):
    pass


class ToggleChatLog(Message):
    pass


class RunCallback(Message):
    def __init__(self, callback: Callable[[], None], delay_ms: int = 0) -> None:
        self.callback = callback
        self.delay_ms = delay_ms
        super().__init__()


class LoadVoiceFrames(Message):
    def __init__(
        self,
        frames: dict[str, BuddyFrame],
        mood_frames: dict[str, dict[str, BuddyFrame]] | None = None,
    ) -> None:
        self.frames = frames
        self.mood_frames = mood_frames or {}
        super().__init__()


class ClearVoiceFrames(Message):
    pass


class SetMood(Message):
    def __init__(self, mood: str) -> None:
        self.mood = mood
        super().__init__()


class RequestExit(Message):
    pass


class OpenSelectionModal(Message):
    def __init__(
        self,
        title: str,
        groups: list[SelectionGroup],
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> None:
        self.title = title
        self.groups = groups
        self.on_save = on_save
        super().__init__()


class OpenConfirmModal(Message):
    def __init__(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> None:
        self.title = title
        self.body = body
        self.on_result = on_result
        super().__init__()


class OpenCloudModal(Message):
    def __init__(self, state: Any, on_result: Callable[[Any], None]) -> None:
        self.state = state
        self.on_result = on_result
        super().__init__()


class OpenOptionsModal(Message):
    def __init__(self, state: Any, on_result: Callable[[Any], None]) -> None:
        self.state = state
        self.on_result = on_result
        super().__init__()


class OpenVoiceModal(Message):
    def __init__(self, state: Any, on_result: Callable[[Any], None]) -> None:
        self.state = state
        self.on_result = on_result
        super().__init__()


class LoadChatHistory(Message):
    def __init__(
        self, entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        self.entries = entries
        super().__init__()


class UpdateEnvironmentState(Message):
    def __init__(self, snapshot: EnvironmentSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__()


# --- Widgets ---


class HeaderWidget(Static):
    """Centered buddy name with border lines."""

    def __init__(self, buddy_name: str) -> None:
        super().__init__(id="header")
        self._buddy_name = buddy_name

    def on_mount(self) -> None:
        self._refresh_header()

    def on_resize(self) -> None:
        self._refresh_header()

    def _refresh_header(self) -> None:
        width = self.size.width or 40
        label = f" {self._buddy_name} "
        pad = max(0, (width - len(label)) // 2)
        self.update(f"{'─' * pad}{label}{'─' * pad}")


class SpeechBubbleWidget(VerticalScroll):
    """Scrollable speech bubble with typing animation."""

    def __init__(self) -> None:
        super().__init__(id="speech-scroll")
        self._body: Static = Static(id="speech")
        self._full_text: str = ""
        self._bubble: SpeechBubble | None = None
        self._source_bubble: SpeechBubble | None = None
        self._typing_index: int = 0
        self._typing_timer: Timer | None = None
        self._hide_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield self._body

    @property
    def is_active(self) -> bool:
        return self._bubble is not None

    @property
    def current_bubble(self) -> SpeechBubble | None:
        return self._bubble

    @property
    def source_bubble(self) -> SpeechBubble | None:
        return self._source_bubble

    def start_typing(
        self, bubble: SpeechBubble, source: SpeechBubble | None = None
    ) -> None:
        self._prime(bubble, source, typing_index=0)
        self._typing_timer = self.set_interval(0.03, self._advance_typing)

    def show_immediate(
        self, bubble: SpeechBubble, source: SpeechBubble | None = None
    ) -> None:
        self._prime(bubble, source, typing_index=max(0, len(bubble.text) - 1))
        self._start_auto_hide()

    def swap_variant(self, bubble: SpeechBubble) -> None:
        if self._bubble is None:
            return
        self._bubble = bubble
        self._render_partial()

    def _prime(
        self, bubble: SpeechBubble, source: SpeechBubble | None, typing_index: int
    ) -> None:
        self._cancel_timers()
        self._bubble = bubble
        self._source_bubble = source or bubble
        self._full_text = bubble.text
        self._typing_index = typing_index
        self.display = True
        self._render_partial()

    def _advance_typing(self) -> None:
        self._typing_index += 1
        if self._typing_index >= len(self._full_text):
            if self._typing_timer:
                self._typing_timer.stop()
                self._typing_timer = None
            self._render_partial()
            self._start_auto_hide()
        else:
            self._render_partial()

    def _render_partial(self) -> None:
        if not self._bubble:
            return
        partial = dataclasses.replace(
            self._bubble, text=self._full_text[: self._typing_index + 1]
        )
        self._body.update("\n".join(partial.render()))
        self.scroll_end(animate=False)

    def _start_auto_hide(self) -> None:
        if self._bubble and self._bubble.persistent:
            return
        delay = max(_BUBBLE_HOLD_MIN_S, len(self._full_text) * _BUBBLE_HOLD_PER_CHAR_S)
        self._hide_timer = self.set_timer(delay, self._fire_auto_hide)

    @property
    def is_typing(self) -> bool:
        return self._typing_timer is not None

    def _fire_auto_hide(self) -> None:
        self._hide_timer = None
        self.post_message(HideSpeech())

    def hide(self) -> None:
        self._cancel_timers()
        self._bubble = None
        self._source_bubble = None
        self.display = False

    def _cancel_timers(self) -> None:
        if self._typing_timer:
            self._typing_timer.stop()
            self._typing_timer = None
        if self._hide_timer:
            self._hide_timer.stop()
            self._hide_timer = None


_PARTICLE_TICK_S = 0.1
_ENV_POLL_S = 1.0
# Explicit bg style for ParticleSky's blank cells. Custom widgets that
# override render_line don't inherit the CSS background onto unstyled
# segments — the compositor resolves them through a default theme tint
# that makes the sky look one shade lighter than the rest of the panel.
# Painting every blank cell with this explicit bg pins the color.
_SKY_BG = Style.parse("on #1a1a2e")


class BuddyWidget(Static):
    """ASCII buddy art with optional Rich markup and idle blink animation."""

    def __init__(self) -> None:
        super().__init__(id="buddy", markup=True)
        self._custom_frames: dict[str, BuddyFrame] = {}
        self._mood_frames: dict[str, dict[str, BuddyFrame]] = {}
        self._current_mood: str = "default"
        self._blink_timer: Timer | None = None
        self._blink_state: bool = False
        self._is_talking: bool = False
        self._cached_max_width: int = self._compute_max_frame_width()

    def set_custom_frames(
        self,
        frames: dict[str, BuddyFrame],
        mood_frames: dict[str, dict[str, BuddyFrame]] | None = None,
    ) -> None:
        self._custom_frames = frames
        self._mood_frames = mood_frames or {}
        self._cached_max_width = self._compute_max_frame_width()
        self._stop_blink()
        if "idle_alt" in frames and "idle" in frames:
            self._blink_timer = self.set_interval(4.0, self._toggle_blink)
        if not self._is_talking:
            self.show_frame(self._get_frame("idle"))

    def set_mood(self, mood: str) -> None:
        """Switch the active mood key. Re-renders if a different frame set applies."""
        if mood == self._current_mood:
            return
        self._current_mood = mood
        self._cached_max_width = self._compute_max_frame_width()
        if not self._is_talking:
            self.show_frame(self._get_frame("idle"))

    def clear_custom_frames(self) -> None:
        self._custom_frames = {}
        self._mood_frames = {}
        self._current_mood = "default"
        self._cached_max_width = self._compute_max_frame_width()
        self._stop_blink()
        self.show_frame(BuddyFrame.get("idle"))

    def show_frame(self, frame: BuddyFrame) -> None:
        self._is_talking = frame.name == "talking"
        if self._is_talking:
            self._blink_state = False
        self._render_frame(frame)

    def _render_frame(self, frame: BuddyFrame) -> None:
        try:
            self.update("\n".join(frame.lines))
        except MarkupError as exc:
            log.warning(
                "buddy frame %s has unparseable markup (%s); rendering plain",
                frame.name, exc,
            )
            self.update("\n".join(_esc_markup(line) for line in frame.lines))

    def _get_frame(self, name: str) -> BuddyFrame:
        mood_set = self._mood_frames.get(self._current_mood)
        if mood_set and name in mood_set:
            return mood_set[name]
        if name in self._custom_frames:
            return self._custom_frames[name]
        return BuddyFrame.get(name)

    def _toggle_blink(self) -> None:
        if self._is_talking:
            return
        self._blink_state = not self._blink_state
        name = "idle_alt" if self._blink_state else "idle"
        self._render_frame(self._get_frame(name))

    def max_frame_width(self) -> int:
        return self._cached_max_width

    def frame_height(self) -> int:
        # Number of rows in the active frame (custom voice or generic idle).
        frame = (
            self._custom_frames.get("idle")
            or self._custom_frames.get("talking")
            or BuddyFrame.get("idle")
        )
        return len(frame.lines)

    def _compute_max_frame_width(self) -> int:
        frames = self._custom_frames or {
            "idle": BuddyFrame.get("idle"),
            "talking": BuddyFrame.get("talking"),
        }
        widths = [
            Text.from_markup(line).cell_len
            for frame in frames.values()
            for line in frame.lines
        ]
        return max(widths, default=20)

    def _stop_blink(self) -> None:
        if self._blink_timer:
            self._blink_timer.stop()
            self._blink_timer = None
        self._blink_state = False

    def render_line(self, y: int) -> Strip:
        """Delegate to Static for the ASCII art, then overlay any reaction
        particles whose panel-Y falls within the buddy's own region. Only
        overwrites cells that are currently blank spaces — preserves the
        buddy's face glyphs (eyes, mouth, body outline).
        """
        strip = super().render_line(y)
        env_controller = getattr(self.app, "env_controller", None)
        if env_controller is None:
            return strip
        panel = self.parent
        if panel is None:
            return strip
        try:
            buddy_y_offset = int(self.region.y - panel.region.y)
        except Exception:
            return strip
        panel_y = y + buddy_y_offset

        width = self.size.width
        overlays: dict[int, tuple[str, str]] = {}
        for p in env_controller.field.particles:
            px = int(round(p.x))
            py = int(round(p.y))
            if py != panel_y or px < 0 or px >= width:
                continue
            overlays[px] = (p.glyph, p.color)
        if not overlays:
            return strip

        cells: list[tuple[str, Style | None]] = []
        for seg in strip:
            for ch in seg.text:
                cells.append((ch, seg.style))
        # Only overwrite blank cells so the buddy's face stays intact.
        changed = False
        for px, (glyph, color) in overlays.items():
            if 0 <= px < len(cells):
                ch, existing_style = cells[px]
                if ch == " ":
                    bg = existing_style.bgcolor if existing_style else None
                    cells[px] = (glyph, Style(color=color, bgcolor=bg))
                    changed = True
        if not changed:
            return strip

        segments: list[Segment] = []
        run_text = ""
        run_style: Style | None = None
        for ch, st in cells:
            if run_style is not None and st != run_style:
                segments.append(Segment(run_text, run_style))
                run_text = ""
            run_text += ch
            run_style = st
        if run_text:
            segments.append(Segment(run_text, run_style))
        return Strip(segments)


class ParticleSky(Widget):
    """A regular (non-layered, opaque) widget that sits between the header
    and the buddy/bubble row. Renders the sun/moon, cloud-above-buddy, and
    falling particles. Drives the buddy + speech-region CSS offsets so they
    slide together.
    """

    DEFAULT_CSS = """
    ParticleSky {
        width: 100%;
        height: 1fr;
        background: #1a1a2e;
    }
    """

    def __init__(
        self,
        get_buddy: Callable[[], BuddyWidget],
        get_speech_region: Callable[[], Vertical] | None = None,
        get_stage: Callable[[], BuddyStage] | None = None,
        env_controller: BuddyEnvironmentController | None = None,
    ) -> None:
        super().__init__(id="particle-sky")
        self._get_buddy = get_buddy
        self._get_speech_region = get_speech_region
        self._get_stage = get_stage
        # Controller owns motion + field + cloud_drift. If not provided
        # (tests, fallback path), we make one locally so ParticleSky stays
        # self-contained. Phase 2 hoists construction to the app.
        self._env = env_controller or BuddyEnvironmentController()
        self._snapshot: EnvironmentSnapshot | None = None
        self._cached_prop_anchors: tuple[tuple[PropSprite, int, int], ...] = ()
        self._last_buddy_offset: tuple[int, int] = (0, 0)
        self._last_speech_offset: tuple[int, int] = (0, 0)
        self._last_stage_offset: tuple[int, int] = (0, 0)
        # Star field signature: (panel_w, panel_h, max_x, target). Re-populates
        # whenever the panel resizes OR the moon's left edge moves (e.g.
        # the snapshot arrives after the first tick). None = not populated.
        self._starfield_for: tuple[int, int, int, int] | None = None

    @property
    def motion(self) -> BuddyMotion:
        return self._env.motion

    @property
    def env_controller(self) -> BuddyEnvironmentController:
        return self._env

    def on_mount(self) -> None:
        self.set_interval(_PARTICLE_TICK_S, self._sim_tick)

    def update_snapshot(self, snapshot: EnvironmentSnapshot) -> None:
        self._snapshot = snapshot

    def _current_env(self) -> EnvState:
        snap = self._snapshot
        return EnvState.from_inputs(
            weather_data=snap.weather_data if snap else None,
            idle_event=snap.idle_event if snap else None,
            sensitive_suppressed=snap.sensitive_suppressed if snap else False,
        )

    def _sky_panel_y_offset(self) -> int:
        """How many rows the sky widget sits below the panel's top. Used to
        convert widget-local Y (render_line) to panel-relative Y (field).
        """
        try:
            panel = self.parent
            if panel is None:
                return 0
            return int(self.region.y - panel.region.y)
        except Exception:
            return 0

    def _buddy_panel_y(self) -> tuple[float, float]:
        """Panel-relative Y range of the buddy widget: (top, bottom).
        Returns (0, 0) before mount / if lookup fails."""
        try:
            buddy = self._get_buddy()
            panel = self.parent
            if panel is None:
                return 0.0, 0.0
            top = float(buddy.region.y - panel.region.y)
            return top, top + float(buddy.region.height)
        except Exception:
            return 0.0, 0.0

    def _sim_tick(self) -> None:
        env = self._current_env()
        panel_w = max(1, self.size.width)
        sky_h = max(1, self.size.height)
        buddy = self._get_buddy()
        buddy_w = buddy.max_frame_width()
        # BuddyMotion.x runs in [0, slide_w]; the buddy art is centered in
        # his widget, so the visual CSS offset must be shifted by -slide_w/2
        # to keep him inside the panel bounds (which include the resizable
        # chat-log divider).
        slide_w = max(0.0, float(panel_w - buddy_w))
        slide_h = 0.0  # buddy stays planted; horizontal slide only

        motion = self._env.motion
        centered_offset_y = 0
        buddy_x_center_pre = panel_w / 2 + (motion.x - slide_w / 2.0)
        sky_y_offset = self._sky_panel_y_offset()
        weather_y_top = float(sky_y_offset) - 1.0
        weather_y_bound = float(sky_y_offset + sky_h)

        # Reaction anchors move to panel-relative coords — impact at 60% of
        # sky, swirl at sky bottom (unchanged visual until phase 4 where
        # BuddyWidget starts rendering particles in its own rows).
        burst_y = float(sky_y_offset) + max(1.0, float(sky_h) * 0.6)
        swirl_y = float(sky_y_offset) + max(1.0, float(sky_h - 1))

        self._env.tick(
            _PARTICLE_TICK_S,
            slide_w=slide_w,
            slide_h=slide_h,
            panel_w=panel_w,
            panel_h=sky_h,  # field uses panel_h for dust/steam spawn span
            env=env,
            buddy_x=buddy_x_center_pre,
            buddy_y=weather_y_bound,  # steam rises from sky bottom (today's behavior)
            spawn_impact_at=(buddy_x_center_pre, burst_y),
            spawn_swirl_at=(buddy_x_center_pre, swirl_y),
            weather_y_top=weather_y_top,
            weather_y_bound=weather_y_bound,
        )

        centered_offset_x = int(round(motion.x - slide_w / 2.0))
        buddy_x_center = panel_w / 2 + (motion.x - slide_w / 2.0)

        # Prop anchors kept in widget-local Y (render_line already works
        # in widget-local Y). No conversion needed since they aren't stored
        # in the particle field.
        prop_stack = props_for(env) if self._snapshot else ()
        anchors: list[tuple[PropSprite, int, int]] = []
        for prop in prop_stack:
            if prop.follows_buddy:
                anchor_x = int(round(buddy_x_center - prop.width / 2))
                anchor_y = max(0, sky_h - prop.height)
            else:
                anchor_x = max(0, panel_w - prop.width - 2)
                anchor_y = 0
            drift_dx = 0
            if prop.drift_x_amplitude > 0.0:
                drift_dx = int(round(self._env.cloud_drift.offset_x(
                    prop.drift_x_amplitude, prop.drift_phase_offset,
                )))
            anchors.append((
                prop,
                anchor_x + prop.anchor_dx + drift_dx,
                anchor_y + prop.anchor_dy,
            ))
        self._cached_prop_anchors = tuple(anchors)

        # Star field: pre-populate when entering clear+night; re-populate
        # when the panel resizes OR the moon's left edge changes (e.g. the
        # snapshot arrives after the first tick). Stars live in panel-y
        # coords anchored to the sky's top.
        scale = night_star_scale(env)
        if scale > 0.0:
            max_x = panel_w
            if self._cached_prop_anchors:
                prop, ax, _ay = self._cached_prop_anchors[0]
                # ax is panel_w - prop.width - 2; clamp to one cell left of it.
                max_x = max(1, ax - 1)
            base_target = min(70, max(12, (panel_w * sky_h) // 25))
            target = max(1, int(round(base_target * scale)))
            # sig includes target so switching between clear/partly-cloudy
            # night re-populates at the new density.
            sig = (panel_w, sky_h, max_x, target)
            if self._starfield_for != sig:
                self._env.field.populate_starfield(
                    panel_w, sky_h,
                    target_count=target,
                    max_x=max_x,
                    y_top=float(sky_y_offset),
                )
                self._starfield_for = sig
        elif self._starfield_for is not None:
            self._env.field.clear_stars()
            self._starfield_for = None

        next_buddy_offset = (centered_offset_x, centered_offset_y)
        if next_buddy_offset != self._last_buddy_offset:
            self._last_buddy_offset = next_buddy_offset
            buddy.styles.offset = next_buddy_offset

        # Stage offset = drag/release plane. Separate from buddy offset so
        # slide (buddy) and drag (stage) compose instead of overwriting.
        if self._get_stage is not None:
            try:
                stage = self._get_stage()
            except Exception:
                stage = None
            if stage is not None:
                next_stage_offset = (
                    int(round(motion.drag_offset_x)),
                    int(round(motion.drag_offset_y)),
                )
                if next_stage_offset != self._last_stage_offset:
                    self._last_stage_offset = next_stage_offset
                    stage.styles.offset = next_stage_offset

        # Speech-region rides along horizontally so the bubble + tail stay
        # over the buddy as he slides.
        if self._get_speech_region is not None:
            try:
                speech_region = self._get_speech_region()
            except Exception:
                speech_region = None
            if speech_region is not None:
                next_speech_offset = (centered_offset_x, 0)
                if next_speech_offset != self._last_speech_offset:
                    self._last_speech_offset = next_speech_offset
                    speech_region.styles.offset = next_speech_offset

        self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)

        row: list[tuple[str, Any] | None] = [None] * width

        for prop, anchor_x, anchor_y in self._cached_prop_anchors:
            local_py = y - anchor_y
            if not (0 <= local_py < prop.height):
                continue
            line = prop.lines[local_py]
            if prop.invert:
                prop_style = Style(color="#1a1a2e", bgcolor=prop.color)
            else:
                prop_style = style_for(prop.color) + _SKY_BG
            for i, ch in enumerate(line):
                cx = anchor_x + i
                if 0 <= cx < width and ch != " ":
                    row[cx] = (ch, prop_style)

        # Particle Y is panel-relative; convert to widget-local before match.
        sky_y_offset = self._sky_panel_y_offset()
        panel_y = y + sky_y_offset
        for p in self._env.field.particles:
            px = int(round(p.x))
            py = int(round(p.y))
            if py != panel_y or px < 0 or px >= width:
                continue
            row[px] = (p.glyph, style_for(p.color) + _SKY_BG)

        if all(cell is None for cell in row):
            return Strip.blank(width, _SKY_BG)

        segments: list[Segment] = []
        run_text = ""
        run_style: Style | None = None
        for cell in row:
            if cell is None:
                if run_text:
                    segments.append(Segment(run_text, run_style))
                    run_text = ""
                    run_style = None
                segments.append(Segment(" ", _SKY_BG))
            else:
                ch, style = cell
                if run_style is not None and style != run_style:
                    segments.append(Segment(run_text, run_style))
                    run_text = ""
                run_text += ch
                run_style = style
        if run_text:
            segments.append(Segment(run_text, run_style))
        return Strip(segments, width)


_MOOD_COLORS: dict[str, str] = {
    "snarky": "#00ff88",
    "impressed": "#ffcc00",
    "bored": "#888888",
    "concerned": "#ff6666",
    "hyper": "#ff88ff",
    "sleepy": "#6688cc",
}


class StatusBarWidget(Horizontal):
    """Bottom status row: mood-colored status on the left, fixed key hints dock-right."""

    _HINT_TEXT = "F3 options \u00b7 Ctrl+L clear"

    def __init__(self) -> None:
        super().__init__(id="status-bar")
        self._main = Static("Ctrl+C to quit", id="status-main", markup=True)
        self._hint = Static(f"[#666666]{self._HINT_TEXT}[/]", id="status-hint", markup=True)

    def compose(self) -> ComposeResult:
        yield self._main
        yield self._hint

    def set_text(self, text: str) -> None:
        parts = text.split(" | ", maxsplit=1)
        mood = parts[0].lower()
        color = _MOOD_COLORS.get(mood, "#666666")
        if len(parts) > 1:
            markup = f"[{color}]{parts[0]}[/] | {parts[1]}"
        else:
            markup = f"[{color}]{text}[/]"
        self._main.update(markup)


# --- Buddy stage (click / drag / shake) ---


class BuddyStage(Container):
    """Wrapper around ``BuddyWidget`` that owns click, drag, and shake
    detection. Writes drag offset to itself so ambient slide (on
    ``buddy.styles.offset``, driven by ParticleSky) composes additively
    without either plane overwriting the other.
    """

    _DRAG_CELL_THRESHOLD = 4
    _DRAG_HOLD_THRESHOLD_S = 0.15

    class BuddyPoked(Message):
        pass

    class BuddyShaken(Message):
        pass

    def __init__(
        self,
        *children: Widget,
        get_motion: Callable[[], BuddyMotion] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*children, **kwargs)
        self._get_motion = get_motion
        self._mouse_down_at: tuple[int, int] | None = None
        self._mouse_down_ts: float = 0.0
        self._dragging: bool = False
        self._last_mouse_pos: tuple[int, int] | None = None
        self._last_move_ts: float = 0.0

    def bind_motion(self, provider: Callable[[], BuddyMotion]) -> None:
        """Late-bound motion lookup; ParticleSky owns the BuddyMotion
        instance so we can't construct it in ``__init__``.
        """
        self._get_motion = provider

    def _motion(self) -> BuddyMotion | None:
        if self._get_motion is None:
            return None
        try:
            return self._get_motion()
        except Exception:
            return None

    def on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        self.capture_mouse()
        self._mouse_down_at = (event.screen_x, event.screen_y)
        self._mouse_down_ts = time.monotonic()
        self._dragging = False
        self._last_mouse_pos = self._mouse_down_at
        self._last_move_ts = self._mouse_down_ts

    def on_mouse_move(self, event: MouseMove) -> None:
        if self._mouse_down_at is None:
            return
        now = time.monotonic()
        start_x, start_y = self._mouse_down_at
        total_cells = abs(event.screen_x - start_x) + abs(event.screen_y - start_y)
        held_s = now - self._mouse_down_ts
        if not self._dragging:
            if total_cells < self._DRAG_CELL_THRESHOLD and held_s < self._DRAG_HOLD_THRESHOLD_S:
                return
            self._dragging = True
        last_x, last_y = self._last_mouse_pos or (event.screen_x, event.screen_y)
        dx = event.screen_x - last_x
        dy = event.screen_y - last_y
        dt = max(1e-3, now - self._last_move_ts)
        self._last_mouse_pos = (event.screen_x, event.screen_y)
        self._last_move_ts = now
        motion = self._motion()
        if motion is None:
            return
        motion.drag_update(float(dx), float(dy), dt)
        if motion.consume_shake_trigger():
            self.post_message(self.BuddyShaken())
        self.styles.offset = (int(motion.drag_offset_x), int(motion.drag_offset_y))

    def on_mouse_up(self, event: MouseUp) -> None:
        if self._mouse_down_at is None:
            return
        event.stop()
        try:
            self.release_mouse()
        except Exception:
            pass
        was_dragging = self._dragging
        self._mouse_down_at = None
        self._dragging = False
        self._last_mouse_pos = None
        motion = self._motion()
        if motion is None:
            return
        if was_dragging:
            motion.release()
        else:
            motion.poke()
            self.post_message(self.BuddyPoked())

    def force_release(self) -> None:
        """Flush stuck mouse capture (called on modal push/pop + resize)."""
        if self._mouse_down_at is None and not self._dragging:
            return
        try:
            self.release_mouse()
        except Exception:
            pass
        self._mouse_down_at = None
        self._dragging = False
        self._last_mouse_pos = None
        motion = self._motion()
        if motion is not None:
            motion.release()


# --- Divider ---


class DividerBar(Static):
    """1-cell vertical bar between buddy panel and chat log; drag to resize.

    Drag math lives on the app (it owns the clamp bounds and persistence);
    this widget just captures the mouse and translates screen-x deltas into
    new chat-log widths.
    """

    class DragStart(Message):
        pass

    class DragMove(Message):
        def __init__(self, screen_x: int) -> None:
            self.screen_x = screen_x
            super().__init__()

    class DragEnd(Message):
        pass

    def __init__(self) -> None:
        super().__init__("\u2502", id="divider")
        self._dragging = False

    def on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        self._dragging = True
        self.capture_mouse()
        self.post_message(self.DragStart())

    def on_mouse_move(self, event: MouseMove) -> None:
        if not self._dragging:
            return
        self.post_message(self.DragMove(event.screen_x))

    def on_mouse_up(self, event: MouseUp) -> None:
        if not self._dragging:
            return
        event.stop()
        self._dragging = False
        self.release_mouse()
        self.post_message(self.DragEnd())


# --- App ---


class TokenPalApp(App[None]):
    """Main Textual application for TokenPal."""

    CSS_PATH = str(_CSS_PATH)
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("f1", "command_help", "Help", show=False, priority=True),
        Binding("f2", "toggle_chat_log", "Toggle chat log", show=False, priority=True),
        Binding("f3", "command_options", "Options", show=False, priority=True),
        Binding("f4", "command_voice", "Voice", show=False, priority=True),
        Binding("f7", "toggle_buddy", "Toggle buddy (select mode)", show=False, priority=True),
        Binding("ctrl+l", "command_clear", "Clear", show=False, priority=True),
    ]

    def __init__(self, overlay: TextualOverlay) -> None:
        super().__init__()
        self._overlay = overlay
        self._chat_log_user_hidden: bool = False
        self._buddy_user_hidden: bool = False
        self._pending_bubble: SpeechBubble | None = None
        self._last_region_size: tuple[int, int] | None = None
        self._chat_log_lines: list[str] = []
        self._link_urls: list[str] = []
        initial = int(overlay._chat_log_width or _CHAT_LOG_DEFAULT_WIDTH)
        self._chat_log_width: int = max(_CHAT_LOG_MIN_WIDTH, initial)
        self._drag_start_screen_x: int = 0
        self._drag_start_width: int = self._chat_log_width
        self._last_env_snapshot: EnvironmentSnapshot | None = None
        # Shared buddy-environment controller. Owned by the app so multiple
        # widgets (ParticleSky for weather, BuddyWidget for reactions in
        # phase 4) can read particles from the same field.
        self.env_controller = BuddyEnvironmentController()

    def compose(self) -> ComposeResult:
        with Vertical(id="buddy-panel"):
            yield HeaderWidget(self._overlay._buddy_name)
            yield ParticleSky(
                get_buddy=lambda: self.query_one(BuddyWidget),
                get_speech_region=lambda: self.query_one("#speech-region", Vertical),
                get_stage=lambda: self.query_one(BuddyStage),
                env_controller=self.env_controller,
            )
            with Vertical(id="speech-region"):
                yield SpeechBubbleWidget()
            with BuddyStage(id="buddy-stage"):
                yield BuddyWidget()
            yield Input(placeholder="Type a message or /command...", id="user-input")
            yield StatusBarWidget()
        yield DividerBar()
        with VerticalScroll(id="chat-log"):
            yield Static(id="chat-log-content", markup=True)

    def on_mount(self) -> None:
        self._overlay._is_running = True
        self._chat_log_widget = self.query_one("#chat-log-content", Static)
        self._chat_log_scroll = self.query_one("#chat-log", VerticalScroll)
        buddy = self.query_one(BuddyWidget)
        if self._overlay._pending_voice_frames:
            buddy.set_custom_frames(
                self._overlay._pending_voice_frames,
                self._overlay._pending_mood_frames or None,
            )
            self._overlay._pending_voice_frames = None
            self._overlay._pending_mood_frames = None
        else:
            buddy.show_frame(BuddyFrame.get("idle"))
        self._apply_buddy_panel_min_width()
        self._apply_chat_log_width()
        # Late-bind BuddyStage to the shared controller's BuddyMotion so
        # click/drag events can update physics state. Going through the
        # controller (not ParticleSky) keeps ownership unambiguous.
        try:
            stage = self.query_one(BuddyStage)
            stage.bind_motion(lambda: self.env_controller.motion)
        except Exception as exc:
            log.debug("BuddyStage motion binding failed: %s", exc)
        if self._overlay._pending_chat_history is not None:
            pending = self._overlay._pending_chat_history
            self._overlay._pending_chat_history = None
            self.post_message(LoadChatHistory(pending))
        # Pull environment snapshot from the brain at 1 Hz; the particle
        # overlay's own 10 Hz ticker reads the buffered snapshot.
        if self._overlay._env_provider is not None:
            self.set_interval(_ENV_POLL_S, self._tick_environment)
        log.info("TextualOverlay ready")

    def _tick_environment(self) -> None:
        provider = self._overlay._env_provider
        if provider is None:
            return
        try:
            snap = provider()
        except Exception as exc:  # provider is brain code; don't crash UI
            log.debug("environment provider failed: %s", exc)
            return
        # Most ticks the snapshot is identical (weather caches 30 min, idle
        # state is sticky). Skip the cross-thread message + buffer write.
        if snap == self._last_env_snapshot:
            return
        self._last_env_snapshot = snap
        self.post_message(UpdateEnvironmentState(snap))

    def on_update_environment_state(self, message: UpdateEnvironmentState) -> None:
        try:
            sky = self.query_one(ParticleSky)
        except Exception:
            return
        sky.update_snapshot(message.snapshot)

    def _apply_buddy_panel_min_width(self) -> None:
        buddy = self.query_one(BuddyWidget)
        panel = self.query_one("#buddy-panel", Vertical)
        panel.styles.min_width = buddy.max_frame_width() + _BUDDY_PANEL_PADDING

    def _buddy_min_width(self) -> int:
        buddy = self.query_one(BuddyWidget)
        return buddy.max_frame_width() + _BUDDY_PANEL_PADDING

    def _clamp_chat_log_width(self, width: int) -> int:
        """Floor at chat-log min; ceiling so buddy panel keeps its min-width + divider."""
        total = self.size.width or (self._buddy_min_width() + _CHAT_LOG_MIN_WIDTH + 1)
        max_w = max(_CHAT_LOG_MIN_WIDTH, total - self._buddy_min_width() - 1)
        return max(_CHAT_LOG_MIN_WIDTH, min(int(width), max_w))

    def _apply_chat_log_width(self) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        if self._buddy_user_hidden:
            chat_log.styles.width = "1fr"
            return
        chat_log.styles.width = self._chat_log_width

    def on_resize(self, _event: Resize) -> None:
        clamped = self._clamp_chat_log_width(self._chat_log_width)
        if clamped != self._chat_log_width:
            self._chat_log_width = clamped
            self._apply_chat_log_width()
        self._apply_chat_log_visibility()
        self._evict_oversized_bubble()
        self._release_buddy_stage_capture()

    def _release_buddy_stage_capture(self) -> None:
        """Flush any stuck mouse capture on the buddy stage. Called on
        resize and on modal push/pop so a drag that crosses one of those
        transitions doesn't leave the terminal with a dead mouse.
        """
        try:
            stage = self.query_one(BuddyStage)
        except Exception:
            return
        stage.force_release()

    def push_screen(self, screen: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self._release_buddy_stage_capture()
        return super().push_screen(screen, *args, **kwargs)

    def pop_screen(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self._release_buddy_stage_capture()
        return super().pop_screen(*args, **kwargs)

    def _apply_chat_log_visibility(self) -> None:
        if self._buddy_user_hidden:
            chat_log = self.query_one("#chat-log", VerticalScroll)
            chat_log.display = True
            self.query_one(DividerBar).display = False
            return
        if self._chat_log_user_hidden:
            return
        buddy = self.query_one(BuddyWidget)
        threshold = buddy.max_frame_width() + _BUDDY_PANEL_PADDING + _CHAT_LOG_MIN_SPACE
        chat_log = self.query_one("#chat-log", VerticalScroll)
        show = self.size.width >= threshold
        chat_log.display = show
        self.query_one(DividerBar).display = show

    def _evict_oversized_bubble(self) -> None:
        speech = self.query_one(SpeechBubbleWidget)
        if not (speech.is_active or self._pending_bubble):
            return
        region = self.query_one("#speech-region", Vertical)
        region_size = (region.size.width, region.size.height)
        if region_size == self._last_region_size:
            return
        self._last_region_size = region_size
        self._rechoose_active_variant(speech)
        self._promote_pending(speech)

    def _rechoose_active_variant(self, speech: SpeechBubbleWidget) -> None:
        source = speech.source_bubble
        current = speech.current_bubble
        if not (speech.is_active and source and current):
            return
        variant = self._choose_bubble_variant(source)
        if variant is None:
            self.post_message(HideSpeech())
        elif variant.borderless != current.borderless or variant.max_width != current.max_width:
            speech.swap_variant(variant)

    def _promote_pending(self, speech: SpeechBubbleWidget) -> None:
        if not self._pending_bubble or speech.is_active:
            return
        variant = self._choose_bubble_variant(self._pending_bubble)
        if variant is None:
            return
        source = self._pending_bubble
        self._pending_bubble = None
        self._begin_bubble(variant, source=source, skip_typing=True)

    # --- Keyboard shortcuts ---

    def action_command_help(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/help")

    def action_command_clear(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/clear")

    def action_command_options(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/options")

    def action_command_voice(self) -> None:
        if self._overlay._command_callback:
            self._overlay._command_callback("/voice")

    def action_toggle_chat_log(self) -> None:
        chat_log = self.query_one("#chat-log", VerticalScroll)
        new_display = not chat_log.display
        chat_log.display = new_display
        self.query_one(DividerBar).display = new_display
        self._chat_log_user_hidden = not new_display
        if new_display:
            self._apply_chat_log_width()

    def action_toggle_buddy(self) -> None:
        """Hide the buddy panel so the chat log fills the width.

        Intended for the Shift+drag select-and-copy workflow on Windows
        Terminal — with the buddy art gone, rectangular terminal selection
        grabs clean chat-log text. Press F7 again to restore.
        """
        panel = self.query_one("#buddy-panel", Vertical)
        divider = self.query_one(DividerBar)
        chat_log = self.query_one("#chat-log", VerticalScroll)
        now_hidden = panel.display  # about to flip
        panel.display = not now_hidden
        self._buddy_user_hidden = now_hidden
        if now_hidden:
            # Entering select mode: full-width chat log, no divider.
            divider.display = False
            chat_log.display = True
            chat_log.styles.width = "1fr"
        else:
            # Leaving select mode: restore chat log to its stored width
            # and let the visibility rules decide whether to show it.
            self._apply_chat_log_width()
            self._apply_chat_log_visibility()

    # --- Input handling ---

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        log.info("Input: %s", text[:30])
        if text.startswith("/"):
            if self._overlay._command_callback:
                self._overlay._command_callback(text)
        else:
            # Log user message to chat, then send to brain
            self._log_user(text)
            if self._overlay._input_callback:
                self._overlay._input_callback(text)

    _MAX_CHAT_LOG_LINES = 500

    @staticmethod
    def _format_chat_ts(ts_val: float, today_ymd: str) -> str:
        """Short time-of-day for today, ``Mon DD HH:MM`` prefix otherwise."""
        dt = datetime.fromtimestamp(ts_val)
        if dt.strftime("%Y%m%d") == today_ymd:
            return dt.strftime("%I:%M %p")
        return dt.strftime("%b %d %I:%M %p")

    def _compose_log_line(
        self,
        name: str,
        text: str,
        *,
        markup: bool,
        url: str | None,
        ts_label: str,
    ) -> str:
        safe = text if markup else _esc_markup(text)
        line = (
            f"──────────────────────\n\\[{ts_label}]\n"
            f"[#4ade80]{_esc_markup(name)}:[/#4ade80] [#ffffff]{safe}[/#ffffff]"
        )
        if url:
            idx = len(self._link_urls)
            self._link_urls.append(url)
            line += (
                f"\n[underline #5599ff][@click=app.open_chat_link(\"{idx}\")]"
                f"{_esc_markup(url)}[/][/underline #5599ff]"
            )
        return line

    def _trim_chat_log_lines(self) -> None:
        """Trim _chat_log_lines to the cap and garbage-collect _link_urls.

        URL indices embedded in retained lines are rewritten in-place so
        _link_urls doesn't grow unbounded over a long session.
        """
        lines = self._chat_log_lines
        if len(lines) <= self._MAX_CHAT_LOG_LINES:
            return
        del lines[: len(lines) - self._MAX_CHAT_LOG_LINES]
        self._rebuild_link_urls()

    def _rebuild_link_urls(self) -> None:
        """Re-walk surviving lines, re-number any @click indices, and drop
        unreferenced URLs. Cheap — called only on trim."""
        lines = self._chat_log_lines
        if not self._link_urls:
            return
        prefix = '@click=app.open_chat_link("'
        new_urls: list[str] = []
        for i, line in enumerate(lines):
            start = 0
            while True:
                at = line.find(prefix, start)
                if at == -1:
                    break
                idx_start = at + len(prefix)
                idx_end = line.find('"', idx_start)
                if idx_end == -1:
                    break
                try:
                    old_idx = int(line[idx_start:idx_end])
                except ValueError:
                    start = idx_end + 1
                    continue
                if 0 <= old_idx < len(self._link_urls):
                    new_idx = len(new_urls)
                    new_urls.append(self._link_urls[old_idx])
                    line = (
                        line[:idx_start] + str(new_idx) + line[idx_end:]
                    )
                    lines[i] = line
                    start = idx_start + len(str(new_idx)) + 1
                else:
                    start = idx_end + 1
        self._link_urls = new_urls

    def _append_log(
        self, name: str, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        today = datetime.now().strftime("%Y%m%d")
        ts_label = self._format_chat_ts(datetime.now().timestamp(), today)
        line = self._compose_log_line(
            name, text, markup=markup, url=url, ts_label=ts_label,
        )
        self._chat_log_lines.append(line)
        self._trim_chat_log_lines()
        self._chat_log_widget.update("\n".join(self._chat_log_lines))
        self._chat_log_scroll.scroll_end(animate=False)
        cb = self._overlay._chat_persist_callback
        if cb is not None:
            try:
                cb(name, text, url)
            except Exception as exc:
                log.warning("chat persist callback failed: %s", exc)

    def action_open_chat_link(self, link_id: str) -> None:
        idx = int(link_id)
        if 0 <= idx < len(self._link_urls):
            self.open_url(self._link_urls[idx])

    def _log_user(self, text: str) -> None:
        self._append_log("You", text)

    def _log_buddy(self, text: str, *, markup: bool = False, url: str | None = None) -> None:
        name = (self._overlay._voice_name or self._overlay._buddy_name).capitalize()
        self._append_log(name, text, markup=markup, url=url)

    # --- Message handlers (all run on app thread) ---

    def on_show_speech(self, message: ShowSpeech) -> None:
        self._log_buddy(message.bubble.text)
        variant = self._choose_bubble_variant(message.bubble)
        if variant is None:
            self._pending_bubble = message.bubble
            return
        speech = self.query_one(SpeechBubbleWidget)
        current = speech.source_bubble if speech.is_active else None
        # Don't let a transient comment clobber a persistent progress bubble.
        if current is not None and current.persistent and not message.bubble.persistent:
            return
        # Persistent-over-persistent skips typing; everything else clobbers with typing.
        skip = message.bubble.persistent and current is not None and current.persistent
        self._pending_bubble = None
        self._begin_bubble(variant, source=message.bubble, skip_typing=skip)

    def on_hide_speech(self, _message: HideSpeech) -> None:
        self.query_one(SpeechBubbleWidget).hide()
        buddy = self.query_one(BuddyWidget)
        buddy.show_frame(buddy._get_frame("idle"))

    def _begin_bubble(
        self, bubble: SpeechBubble, source: SpeechBubble, skip_typing: bool = False
    ) -> None:
        buddy = self.query_one(BuddyWidget)
        buddy.show_frame(buddy._get_frame("talking"))
        speech = self.query_one(SpeechBubbleWidget)
        if skip_typing:
            speech.show_immediate(bubble, source=source)
        else:
            speech.start_typing(bubble, source=source)

    def _choose_bubble_variant(self, bubble: SpeechBubble) -> SpeechBubble | None:
        # None signals "no variant fits — park as pending until resize-up".
        region = self.query_one("#speech-region", Vertical)
        region_h = region.size.height
        region_w = region.size.width
        if region_h <= 0 or region_w <= 0:
            return bubble
        bordered_max = max(1, min(bubble.max_width, region_w - _SPEECH_SCROLL_PADDING))
        if region_w >= _MIN_BORDERED_REGION_WIDTH:
            bordered = dataclasses.replace(
                bubble, max_width=bordered_max, borderless=False
            )
            if len(bordered.render()) <= region_h:
                return bordered
        borderless = dataclasses.replace(
            bubble,
            max_width=max(1, region_w - _SPEECH_SCROLL_PADDING),
            borderless=True,
        )
        if len(borderless.render()) <= region_h:
            return borderless
        return None

    def on_show_buddy(self, message: ShowBuddy) -> None:
        self.query_one(BuddyWidget).show_frame(message.frame)

    def on_load_voice_frames(self, message: LoadVoiceFrames) -> None:
        self.query_one(BuddyWidget).set_custom_frames(
            message.frames, message.mood_frames or None,
        )
        self._apply_buddy_panel_min_width()

    def on_clear_voice_frames(self, _message: ClearVoiceFrames) -> None:
        self.query_one(BuddyWidget).clear_custom_frames()
        self._apply_buddy_panel_min_width()

    def on_set_mood(self, message: SetMood) -> None:
        self.query_one(BuddyWidget).set_mood(message.mood)

    def on_update_status(self, message: UpdateStatus) -> None:
        self.query_one(StatusBarWidget).set_text(message.text)

    def on_log_buddy_message(self, message: LogBuddyMessage) -> None:
        self._log_buddy(message.text, markup=message.markup, url=message.url)

    def on_log_user_message(self, message: LogUserMessage) -> None:
        self._log_user(message.text)

    def on_clear_log(self, _message: ClearLog) -> None:
        self._chat_log_lines.clear()
        self._link_urls.clear()
        self._chat_log_widget.update("")
        cb = self._overlay._chat_clear_callback
        if cb is not None:
            try:
                cb()
            except Exception as exc:
                log.warning("chat clear callback failed: %s", exc)

    def on_load_chat_history(self, message: LoadChatHistory) -> None:
        """Seed the chat-log widget with persisted rows. Entries are
        (timestamp, speaker, text, url) in chronological order.
        """
        entries = message.entries
        if not entries:
            return
        # Clamp to the widget's in-RAM cap so a big hydration payload doesn't
        # blow past _MAX_CHAT_LOG_LINES.
        if len(entries) > self._MAX_CHAT_LOG_LINES:
            entries = entries[-self._MAX_CHAT_LOG_LINES:]
        today = datetime.now().strftime("%Y%m%d")
        rendered = [
            self._compose_log_line(
                speaker,
                text,
                markup=False,
                url=url,
                ts_label=self._format_chat_ts(ts_val, today),
            )
            for ts_val, speaker, text, url in entries
        ]
        self._chat_log_lines[:0] = rendered
        self._trim_chat_log_lines()
        self._chat_log_widget.update("\n".join(self._chat_log_lines))
        self._chat_log_scroll.scroll_end(animate=False)

    def on_toggle_chat_log(self, _message: ToggleChatLog) -> None:
        self.action_toggle_chat_log()

    def on_run_callback(self, message: RunCallback) -> None:
        if message.delay_ms <= 0:
            message.callback()
        else:
            self.set_timer(message.delay_ms / 1000.0, message.callback)

    def on_request_exit(self, _message: RequestExit) -> None:
        self.exit()

    def _modal_already_active(self) -> bool:
        """True when any ModalScreen is on the stack. Prevents stacking a
        second modal when a user hits the keybinding twice or a launcher
        races an already-open picker."""
        return any(
            isinstance(screen, ModalScreen) for screen in self.screen_stack
        )

    def on_open_selection_modal(self, message: OpenSelectionModal) -> None:
        if self._modal_already_active():
            return
        modal = SelectionModal(message.title, message.groups)
        self.push_screen(modal, message.on_save)

    def on_open_confirm_modal(self, message: OpenConfirmModal) -> None:
        if self._modal_already_active():
            return
        modal = ConfirmModal(message.title, message.body)
        self.push_screen(modal, message.on_result)

    def on_open_cloud_modal(self, message: OpenCloudModal) -> None:
        if self._modal_already_active():
            return
        from tokenpal.ui.cloud_modal import CloudModal

        modal = CloudModal(message.state)
        self.push_screen(modal, message.on_result)

    def on_open_options_modal(self, message: OpenOptionsModal) -> None:
        if self._modal_already_active():
            return
        from tokenpal.ui.options_modal import OptionsModal

        modal = OptionsModal(message.state)
        self.push_screen(modal, message.on_result)

    def on_open_voice_modal(self, message: OpenVoiceModal) -> None:
        if self._modal_already_active():
            return
        from tokenpal.ui.voice_modal import VoiceModal

        modal = VoiceModal(message.state)
        self.push_screen(modal, message.on_result)

    # --- Divider drag ---

    def on_divider_bar_drag_start(self, _message: DividerBar.DragStart) -> None:
        self._drag_start_width = self._chat_log_width
        self._drag_start_screen_x = 0  # set on first DragMove

    def on_divider_bar_drag_move(self, message: DividerBar.DragMove) -> None:
        if self._drag_start_screen_x == 0:
            self._drag_start_screen_x = message.screen_x
            return
        delta = message.screen_x - self._drag_start_screen_x
        # Chat log is on the right edge: dragging right shrinks it.
        proposed = self._drag_start_width - delta
        clamped = self._clamp_chat_log_width(proposed)
        if clamped == self._chat_log_width:
            return
        self._chat_log_width = clamped
        self._apply_chat_log_width()

    def on_divider_bar_drag_end(self, _message: DividerBar.DragEnd) -> None:
        self._drag_start_screen_x = 0
        self._overlay._persist_chat_log_width(self._chat_log_width)

    def on_buddy_stage_buddy_poked(self, _message: BuddyStage.BuddyPoked) -> None:
        callback = self._overlay._buddy_reaction_callback
        if callback is not None:
            try:
                callback("poke")
            except Exception:
                log.exception("buddy reaction callback (poke) raised")

    def on_buddy_stage_buddy_shaken(self, _message: BuddyStage.BuddyShaken) -> None:
        callback = self._overlay._buddy_reaction_callback
        if callback is not None:
            try:
                callback("shake")
            except Exception:
                log.exception("buddy reaction callback (shake) raised")


# --- Overlay ---


@register_overlay
class TextualOverlay(AbstractOverlay):
    overlay_name = "textual"
    platforms = ("windows", "darwin", "linux")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._buddy_name = config.get("buddy_name", "TokenPal")
        self._voice_name: str = ""
        self._app: TokenPalApp | None = None
        self._is_running = False
        self._input_callback: Callable[[str], None] | None = None
        self._command_callback: Callable[[str], None] | None = None
        self._buddy_reaction_callback: Callable[[str], None] | None = None
        self._pending_voice_frames: dict[str, BuddyFrame] | None = None
        self._pending_mood_frames: (
            dict[str, dict[str, BuddyFrame]] | None
        ) = None
        self._chat_log_width: int = int(
            config.get("chat_log_width") or _CHAT_LOG_DEFAULT_WIDTH
        )
        # Persist hooks wired by app.py once the MemoryStore is live.
        self._chat_persist_callback: (
            Callable[[str, str, str | None], None] | None
        ) = None
        self._chat_clear_callback: Callable[[], None] | None = None
        # Pending chat-history payload — app.py may hand us rows before
        # run_loop() starts, so we stash them and on_mount drains the buffer.
        self._pending_chat_history: (
            list[tuple[float, str, str, str | None]] | None
        ) = None
        # Environment-snapshot provider (brain.environment_snapshot or similar).
        # The overlay polls this on a 1 Hz Textual interval; if None, the
        # particle field still runs but only ambient dust spawns.
        self._env_provider: Callable[[], EnvironmentSnapshot] | None = None

    def _persist_chat_log_width(self, width: int) -> None:
        """Write the user's chosen chat-log width to config.toml (fire-and-forget)."""
        try:
            from tokenpal.config.ui_writer import set_chat_log_width

            set_chat_log_width(width)
        except Exception as exc:
            log.warning("failed to persist chat_log_width=%d: %s", width, exc)

    def _post(self, message: Message) -> None:
        """Post a message to the app. Thread-safe, no-op if app not ready."""
        if self._app and self._is_running:
            self._app.post_message(message)

    def setup(self) -> None:
        self._app = TokenPalApp(self)

    def show_buddy(self, frame: BuddyFrame) -> None:
        self._post(ShowBuddy(frame))

    def show_speech(self, bubble: SpeechBubble) -> None:
        self._post(ShowSpeech(bubble))

    def hide_speech(self) -> None:
        self._post(HideSpeech())

    def update_status(self, text: str) -> None:
        self._post(UpdateStatus(text))

    def load_voice_frames(
        self,
        frames: dict[str, BuddyFrame],
        mood_frames: dict[str, dict[str, BuddyFrame]] | None = None,
    ) -> None:
        if not self._is_running:
            self._pending_voice_frames = frames
            self._pending_mood_frames = mood_frames or None
            return
        self._post(LoadVoiceFrames(frames, mood_frames))

    def clear_voice_frames(self) -> None:
        self._post(ClearVoiceFrames())

    def set_mood(self, mood: str) -> None:
        """Post a mood swap to the overlay. No-op if not running."""
        if not self._is_running:
            return
        self._post(SetMood(mood))

    def log_buddy_message(
        self, text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        self._post(LogBuddyMessage(text, markup=markup, url=url))

    def log_user_message(self, text: str) -> None:
        self._post(LogUserMessage(text))

    def clear_log(self) -> None:
        self._post(ClearLog())

    def toggle_chat_log(self) -> None:
        self._post(ToggleChatLog())

    def set_input_callback(self, callback: Callable[[str], None]) -> None:
        self._input_callback = callback

    def set_command_callback(self, callback: Callable[[str], None]) -> None:
        self._command_callback = callback

    def set_buddy_reaction_callback(self, callback: Callable[[str], None]) -> None:
        """Invoked with "poke" or "shake" when the user interacts with the
        ASCII buddy. Callback runs on the UI thread — it should enqueue
        into the brain (``Brain.on_buddy_poked`` / ``on_buddy_shaken``)
        and return immediately.
        """
        self._buddy_reaction_callback = callback

    def run_loop(self) -> None:
        if self._app:
            self._app.run()

    def schedule_callback(
        self, callback: Callable[[], None], delay_ms: int = 0
    ) -> None:
        self._post(RunCallback(callback, delay_ms))

    def open_selection_modal(
        self,
        title: str,
        groups: Any,
        on_save: Callable[[dict[str, list[str]] | None], None],
    ) -> bool:
        if not (self._app and self._is_running):
            return False
        self._post(OpenSelectionModal(title, list(groups), on_save))
        return True

    def open_confirm_modal(
        self,
        title: str,
        body: str,
        on_result: Callable[[bool], None],
    ) -> bool:
        if not (self._app and self._is_running):
            return False
        self._post(OpenConfirmModal(title, body, on_result))
        return True

    def open_cloud_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /cloud settings modal. Result is CloudModalResult or None."""
        if not (self._app and self._is_running):
            return False
        self._post(OpenCloudModal(state, on_result))
        return True

    def open_options_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /options umbrella modal. Result is OptionsModalResult or None."""
        if not (self._app and self._is_running):
            return False
        self._post(OpenOptionsModal(state, on_result))
        return True

    def open_voice_modal(
        self,
        state: Any,
        on_result: Callable[[Any], None],
    ) -> bool:
        """Open the /voice management modal. Result is VoiceModalResult or None."""
        if not (self._app and self._is_running):
            return False
        self._post(OpenVoiceModal(state, on_result))
        return True

    def load_chat_history(
        self,
        entries: list[tuple[float, str, str, str | None]],
    ) -> None:
        """Seed the chat-log widget with persisted rows before live traffic.

        Called from app.py after overlay.setup() but before run_loop() — the
        app isn't mounted yet, so we stash the payload and on_mount drains it.
        """
        if not self._is_running:
            self._pending_chat_history = entries
            return
        self._post(LoadChatHistory(entries))

    def set_environment_provider(
        self, provider: Callable[[], EnvironmentSnapshot] | None,
    ) -> None:
        """Wire the brain's environment_snapshot getter. Called by app.py
        after Brain construction; the overlay's app starts a 1 Hz poll on
        on_mount."""
        self._env_provider = provider

    def set_chat_persist_callback(
        self,
        persist: Callable[[str, str, str | None], None] | None,
        clear: Callable[[], None] | None,
    ) -> None:
        """Wire chat-log write-through. ``persist`` is invoked after each
        live line lands; ``clear`` when /clear wipes the widget."""
        self._chat_persist_callback = persist
        self._chat_clear_callback = clear

    def teardown(self) -> None:
        self._is_running = False
        if self._app:
            self._app.post_message(RequestExit())
