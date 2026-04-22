"""Contract tests for the UI adapter seam.

Every overlay registered with ``@register_overlay`` must fully implement
``AbstractOverlay`` — no abstract methods left over, every capability
method callable even on overlays that choose to no-op.

These tests prove the brain can call the full adapter surface on any
overlay without ``hasattr`` probing. Plan: plans/new-ui-new-me.md.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

# Importing the overlay modules is what registers them. Do it explicitly
# so the registry is populated no matter the pytest collection order.
import tokenpal.ui.console_overlay  # noqa: F401
import tokenpal.ui.textual_overlay  # noqa: F401
import tokenpal.ui.tk_overlay  # noqa: F401
from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.buddy_environment import EnvironmentSnapshot
from tokenpal.ui.registry import _OVERLAY_REGISTRY

def _dummy_snapshot() -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        weather_data=None, idle_event=None, sensitive_suppressed=False,
    )


_BRAIN_INVOKED_METHODS: tuple[tuple[str, tuple[Any, ...]], ...] = (
    # Lifecycle
    ("setup", ()),
    ("teardown", ()),
    # Speech / frames
    ("show_buddy", (BuddyFrame.get("idle"),)),
    ("show_speech", (SpeechBubble("hello"),)),
    ("hide_speech", ()),
    # Status / chat
    ("update_status", ("ok",)),
    ("log_user_message", ("hi",)),
    ("log_buddy_message", ("hey",)),
    ("clear_log", ()),
    ("load_chat_history", ([],)),
    # Voice
    ("load_voice_frames", ({}, None)),
    ("clear_voice_frames", ()),
    ("set_mood", ("neutral",)),
    ("set_voice_name", ("pal",)),
    # Chat-pane control
    ("toggle_chat_log", ()),
    # Environment / persistence hooks
    ("set_environment_provider", (_dummy_snapshot,)),
    ("set_chat_persist_callback", (lambda s, t, u: None, lambda: None)),
    # Callback wiring
    ("set_input_callback", (lambda s: None,)),
    ("set_command_callback", (lambda s: None,)),
    ("set_buddy_reaction_callback", (lambda s: None,)),
)

_MODAL_METHODS: tuple[tuple[str, tuple[Any, ...]], ...] = (
    ("open_selection_modal", ("title", [], lambda r: None)),
    ("open_confirm_modal", ("title", "body", lambda r: None)),
    ("open_cloud_modal", (None, lambda r: None)),
    ("open_options_modal", (None, lambda r: None)),
    ("open_voice_modal", (None, lambda r: None)),
)


def _all_overlay_classes() -> list[type[AbstractOverlay]]:
    return list(_OVERLAY_REGISTRY.values())


@pytest.mark.parametrize("overlay_cls", _all_overlay_classes())
def test_overlay_has_no_abstract_methods(overlay_cls: type[AbstractOverlay]) -> None:
    """Every registered overlay must satisfy the ABC at construction time."""
    leftover: frozenset[str] = getattr(
        overlay_cls, "__abstractmethods__", frozenset(),
    )
    assert not leftover, (
        f"{overlay_cls.__name__} leaves abstract methods unimplemented: "
        f"{sorted(leftover)}"
    )


@pytest.mark.parametrize("overlay_cls", _all_overlay_classes())
def test_overlay_exposes_full_adapter_surface(
    overlay_cls: type[AbstractOverlay],
) -> None:
    """Every brain-invoked method is present on every overlay."""
    for name, _args in (*_BRAIN_INVOKED_METHODS, *_MODAL_METHODS):
        assert hasattr(overlay_cls, name), (
            f"{overlay_cls.__name__} is missing `{name}` — the brain "
            f"may hit this; default should live in AbstractOverlay."
        )
        method = getattr(overlay_cls, name)
        assert callable(method), f"{overlay_cls.__name__}.{name} is not callable"


def test_console_overlay_accepts_full_adapter_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Smoke test: every adapter method on the console overlay runs without
    raising on a pre-setup instance. Proves the no-op defaults are safe to
    call even when the overlay hasn't built its window yet."""
    from tokenpal.ui.console_overlay import ConsoleOverlay
    overlay = ConsoleOverlay(config={})
    for name, args in _BRAIN_INVOKED_METHODS:
        if name in {"setup", "teardown"}:
            continue  # these mutate terminal state; skip in unit tests
        method = getattr(overlay, name)
        method(*args)

    for name, args in _MODAL_METHODS:
        result = getattr(overlay, name)(*args)
        assert result is False, (
            f"ConsoleOverlay.{name} should return False (no modal support)"
        )
    capsys.readouterr()  # drain any render output so it doesn't pollute


def test_base_module_documents_adapter_seam() -> None:
    """The base module's docstring is where future readers will look for
    the adapter contract. Keep the seam signposted."""
    import tokenpal.ui.base as base_mod
    module_doc = inspect.getdoc(base_mod) or ""
    assert "adapter seam" in module_doc.lower()
