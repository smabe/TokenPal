"""Overlay discovery and registration."""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import Any

from tokenpal.ui.base import AbstractOverlay
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_OVERLAY_REGISTRY: dict[str, type[AbstractOverlay]] = {}

_TEXTUAL_FALLBACK = "textual"


def _qt_unavailable_reason() -> str | None:
    """Return a short human-readable reason if Qt can't run here, else
    None. Used by the silent-fallback path in ``resolve_overlay`` when
    the user asks for ``qt`` but the host can't actually show a window.
    """
    if os.environ.get("TOKENPAL_HEADLESS") == "1":
        return "TOKENPAL_HEADLESS=1"
    if "qt" not in _OVERLAY_REGISTRY:
        return "PySide6 not installed (tokenpal[desktop] extra)"
    # On Linux, a missing DISPLAY / WAYLAND_DISPLAY means no compositor
    # to attach to. macOS and Windows always have a window server when a
    # user session is live.
    if current_platform() == "linux":
        if not (os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY")):
            return "no DISPLAY / WAYLAND_DISPLAY"
    return None


def register_overlay(cls: type[AbstractOverlay]) -> type[AbstractOverlay]:
    """Decorator. Registers a concrete overlay implementation."""
    _OVERLAY_REGISTRY[cls.overlay_name] = cls
    return cls


def list_overlays() -> list[type[AbstractOverlay]]:
    """Return every registered overlay class. Call ``discover_overlays()``
    first if you need the full set, not just whatever's been imported."""
    return list(_OVERLAY_REGISTRY.values())


def discover_overlays() -> None:
    """Import all modules under tokenpal.ui so decorators fire."""
    import tokenpal.ui as ui_pkg

    for _importer, modname, _ispkg in pkgutil.walk_packages(
        ui_pkg.__path__, prefix=ui_pkg.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
        except ImportError as e:
            log.debug("Skipping overlay module %s: %s", modname, e)


def resolve_overlay(config: dict[str, Any]) -> AbstractOverlay:
    """Pick the overlay matching config or auto-detect."""
    overlay_name = config.get("overlay", "auto")
    plat = current_platform()

    if overlay_name == "auto":
        # Prefer textual, then platform-specific, fall back to tkinter
        for preferred in ("textual", None):
            for name, cls in _OVERLAY_REGISTRY.items():
                if name == "tkinter":
                    continue
                if preferred and name != preferred:
                    continue
                if plat in cls.platforms:
                    log.info("Auto-selected overlay: %s", cls.__name__)
                    return cls(config)
        overlay_name = "tkinter"

    if overlay_name == "qt":
        reason = _qt_unavailable_reason()
        if reason is not None:
            log.info(
                "qt overlay unavailable (%s) — falling back to textual", reason,
            )
            overlay_name = _TEXTUAL_FALLBACK

    selected: type[AbstractOverlay] | None = _OVERLAY_REGISTRY.get(overlay_name)
    if selected is None:
        available = list(_OVERLAY_REGISTRY.keys())
        raise RuntimeError(f"Unknown overlay '{overlay_name}'. Available: {available}")

    log.info("Using overlay: %s (%s)", selected.__name__, overlay_name)
    return selected(config)
