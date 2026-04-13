"""Overlay discovery and registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from tokenpal.ui.base import AbstractOverlay
from tokenpal.util.platform import current_platform

log = logging.getLogger(__name__)

_OVERLAY_REGISTRY: dict[str, type[AbstractOverlay]] = {}


def register_overlay(cls: type[AbstractOverlay]) -> type[AbstractOverlay]:
    """Decorator. Registers a concrete overlay implementation."""
    _OVERLAY_REGISTRY[cls.overlay_name] = cls
    return cls


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

    cls = _OVERLAY_REGISTRY.get(overlay_name)
    if cls is None:
        available = list(_OVERLAY_REGISTRY.keys())
        raise RuntimeError(f"Unknown overlay '{overlay_name}'. Available: {available}")

    log.info("Using overlay: %s (%s)", cls.__name__, overlay_name)
    return cls(config)
