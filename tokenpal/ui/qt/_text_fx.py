"""Shared text-effect helpers for the transparent Qt surfaces.

The buddy's chat dock and history window sit frameless + translucent on
top of arbitrary wallpaper. Flat text (light or dark) disappears against
busy backgrounds. These helpers centralize the two tricks we use to keep
glyphs legible:

1. ``apply_drop_shadow(widget)`` — attaches a ``QGraphicsDropShadowEffect``
   sized so the halo reads on both bright and dark wallpapers. Applied
   once per widget at construction; Qt re-composites on every paint so
   there's no per-draw cost on top of the effect itself.

2. ``glass_pill_stylesheet(...)`` — returns a stylesheet string for a
   ``QLineEdit`` or similar that reads as a "liquid-glass"-style pill:
   faint 1 px border, low-alpha fill, rounded corners. Qt doesn't expose
   the macOS ``NSVisualEffectView`` backdrop-blur without native
   embedding, so we fake it via alpha + border; a later phase could swap
   in a real blur subview on macOS if this isn't convincing enough.
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from tokenpal.config.schema import FontConfig

# Unicode block-drawing chars rendered as filled rects rather than font
# glyphs. Consolas (Win11 default mono) rasterizes the shaded blocks as
# sparse dot grids that read as visual noise on translucent surfaces;
# fill matches the smoother look Menlo gives us on macOS without forcing
# a font choice. Light/medium shades (░ ▒) are intentionally NOT painted
# here — their meaning is context-dependent (shadow on the moon, halo on
# the sun) so a uniform solid-fill rule produces the wrong look for one
# sprite or the other. Letting them fall back to drawText keeps the
# font's natural translucent-dot appearance which works as atmospheric
# fade in either context.
_BLOCK_FULL = "█"
_BLOCK_DARK_SHADE = "▓"
_BLOCK_UPPER_HALF = "▀"
_BLOCK_LOWER_HALF = "▄"
_DARK_SHADE_DARKEN = 120  # ~17% darker


def paint_block_char(
    painter: QPainter, ch: str, rect: QRect, color: QColor,
) -> bool:
    """Paint ``ch`` into ``rect`` as a solid/shaded fill, bypassing the
    font. Returns False if ``ch`` isn't a recognized block char so the
    caller can fall back to ``drawText``.

    Block chars handled: U+2580 ▀, U+2584 ▄, U+2588 █, U+2593 ▓.
    """
    if ch == _BLOCK_FULL:
        painter.fillRect(rect, color)
        return True
    if ch == _BLOCK_DARK_SHADE:
        painter.fillRect(rect, color.darker(_DARK_SHADE_DARKEN))
        return True
    half = rect.height() // 2
    if ch == _BLOCK_UPPER_HALF:
        painter.fillRect(rect.x(), rect.y(), rect.width(), half, color)
        return True
    if ch == _BLOCK_LOWER_HALF:
        painter.fillRect(
            rect.x(), rect.y() + half,
            rect.width(), rect.height() - half, color,
        )
        return True
    return False


def render_sprite_pixmap(
    lines: tuple[str, ...],
    color: QColor,
    *,
    cell_w: int,
    line_h: int,
    font: QFont,
    dpr: float = 1.0,
    supersample: int = 2,
) -> QPixmap:
    """Rasterize an ASCII-art sprite to a ``QPixmap`` cached for later
    ``drawPixmap`` use under arbitrary scale/rotation.

    Source render is oversampled by ``dpr * supersample`` so the bilinear
    filter has headroom. The returned pixmap's ``devicePixelRatio`` is
    ``dpr``, so subsequent ``drawPixmap(target_rect, pix)`` consumes
    ``target_rect`` in logical pixels.
    """
    if not lines:
        return QPixmap()
    rows = len(lines)
    cols = max(len(line) for line in lines)
    scale = max(dpr * supersample, 1.0)
    phys_w = max(int(math.ceil(cols * cell_w * scale)), 1)
    phys_h = max(int(math.ceil(rows * line_h * scale)), 1)

    image = QImage(phys_w, phys_h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.scale(scale, scale)
    painter.setFont(font)
    painter.setPen(color)
    for row, line in enumerate(lines):
        baseline = (row + 1) * line_h
        for col, ch in enumerate(line):
            if ch == " ":
                continue
            cx = col * cell_w
            rect = QRect(cx, baseline - line_h, cell_w + 1, line_h)
            if not paint_block_char(painter, ch, rect, color):
                painter.drawText(cx, baseline, ch)
    painter.end()

    if supersample > 1:
        target_w = max(int(math.ceil(cols * cell_w * dpr)), 1)
        target_h = max(int(math.ceil(rows * line_h * dpr)), 1)
        image = image.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def apply_drop_shadow(
    widget: QWidget,
    *,
    blur: int = 6,
    offset: tuple[int, int] = (0, 1),
    color: QColor | None = None,
) -> QGraphicsDropShadowEffect:
    """Attach a drop-shadow graphics effect to ``widget``.

    Returns the effect so callers can tune blur / color after the fact.
    Intentionally conservative defaults: small offset + medium blur halos
    the glyph without softening it into mush on high-DPI displays.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(offset[0], offset[1])
    effect.setColor(color or QColor(0, 0, 0, 220))
    widget.setGraphicsEffect(effect)
    return effect


def glass_pill_stylesheet(
    *,
    radius: int = 14,
    fg: str = "#ffffff",
    placeholder: str = "rgba(255, 255, 255, 0.55)",
) -> str:
    """Return a stylesheet for a liquid-glass-style QLineEdit pill.

    Fill: low-alpha white tint. Border: 1 px white at ~0.28 alpha. Focus
    bumps both a notch so the user sees the cursor land. Padding keeps
    text off the border — change in tandem with fixed-height callers.
    """
    return f"""
    QLineEdit {{
        background: rgba(255, 255, 255, 0.12);
        color: {fg};
        border: 1px solid rgba(255, 255, 255, 0.28);
        border-radius: {radius}px;
        padding: 6px 12px;
        selection-background-color: rgba(255, 255, 255, 0.35);
    }}
    QLineEdit:focus {{
        background: rgba(255, 255, 255, 0.18);
        border: 1px solid rgba(255, 255, 255, 0.55);
    }}
    QLineEdit::placeholder {{
        color: {placeholder};
    }}
    """


def glass_button_stylesheet(*, radius: int = 12) -> str:
    """Glass-pill styling for a QPushButton / clickable QLabel."""
    return f"""
    QPushButton, QLabel {{
        background: rgba(255, 255, 255, 0.12);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.28);
        border-radius: {radius}px;
        padding: 2px 14px;
    }}
    QPushButton:hover {{
        background: rgba(255, 255, 255, 0.22);
    }}
    """


def glass_scrollbar_stylesheet() -> str:
    """Return a stylesheet that tones the default Qt scrollbar chrome
    down to match the transparent glass aesthetic of the history window.
    """
    return """
    QScrollBar:vertical {
        background: transparent;
        width: 8px;
        margin: 2px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255, 255, 255, 0.25);
        border-radius: 3px;
        min-height: 24px;
    }
    QScrollBar::handle:vertical:hover {
        background: rgba(255, 255, 255, 0.45);
    }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0;
    }
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {
        background: transparent;
    }
    """


def qt_font_from_config(
    cfg: FontConfig,
    *,
    fallback_family: str = "",
    fallback_size: int = 13,
) -> QFont:
    """Build a ``QFont`` from a ``FontConfig``, filling holes from fallbacks.

    Empty ``cfg.family`` → ``fallback_family`` → Qt's platform default.
    ``cfg.size_pt`` of 0 → ``fallback_size``.
    """
    family = cfg.family or fallback_family
    size = cfg.size_pt if cfg.size_pt > 0 else fallback_size
    font = QFont(family) if family else QFont()
    font.setPointSize(size)
    font.setBold(cfg.bold)
    font.setItalic(cfg.italic)
    font.setUnderline(cfg.underline)
    return font


def transparent_window_flags() -> Qt.WindowType:
    """Shared window-flag bundle for the frameless transparent surfaces.

    Keeping this central means the dock and the history window pick up
    the same always-on-top + don't-steal-focus behavior. On macOS we
    drop ``Qt.Tool`` because the NSWindow utility-panel mapping auto-
    hides when the app loses focus — same rationale as ``BuddyWindow``.
    Accessory mode (``apply_macos_accessory_mode``) already keeps the
    buddy off the Dock, so ``Tool`` isn't needed there.
    """
    flags = (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
    )
    if sys.platform != "darwin":
        flags |= Qt.WindowType.Tool
    return flags
