"""Every outcome of the macOS selected-text read, driven through a fake
accessibility bridge so the cases run on any host.

The fake records call order, which is what the two order-critical invariants
need: a secure field is refused before any value is read, and a sensitive
source app is refused before the bridge is touched at all.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests._helpers import assert_no_leak
from tokenpal.actions.base import ActionResult
from tokenpal.desktop.permissions import responsible_host
from tokenpal.desktop.selected_text import (
    _ERR_API_DISABLED,
    _ERR_CANNOT_COMPLETE,
    SelectedText,
    capture_selection,
    read_selected_text,
    source_app,
)
from tokenpal.util.macos_windows import Window

FIXTURE = "the quick brown fox jumped over the lazy dog"

_OK = 0
_NO_VALUE = -25212
_UNSUPPORTED = -25205

_APP_EL = "app-element"
_FOCUSED = "focused-element"

# (pid, owner, title, layer). pid 1 is this process in every test (fixture below).
_TERMINAL: Window = (2, "Orca", "zsh", 0)
_TEXTEDIT: Window = (7, "TextEdit", "notes.txt", 0)
_SAFARI: Window = (9, "Safari", "Apple", 0)
_QT_CHAT: Window = (1, "Python", "TokenPal", 8)


class FakeBridge:
    """``AXBridge`` over a scripted attribute table.

    *attrs* maps element -> attribute name -> ``(err, value)``. A missing
    entry answers ``kAXErrorAttributeUnsupported`` with no value.
    """

    def __init__(
        self,
        *,
        windows: list[Window] | None = None,
        attrs: dict[str, dict[str, tuple[int, Any]]] | None = None,
    ) -> None:
        self._windows = windows if windows is not None else [_TERMINAL, _TEXTEDIT]
        self._attrs = attrs or {}
        self.reads: list[tuple[str, str]] = []
        self.timeouts: list[tuple[str, float]] = []

    def windows(self) -> list[Window]:
        return list(self._windows)

    def application(self, pid: int) -> Any:
        return _APP_EL

    def set_timeout(self, element: Any, seconds: float) -> None:
        self.timeouts.append((element, seconds))

    def attribute(self, element: Any, name: str) -> tuple[int, Any]:
        self.reads.append((element, name))
        return self._attrs.get(element, {}).get(name, (_UNSUPPORTED, None))


def _bridge(
    focused_attrs: dict[str, tuple[int, Any]] | None = None,
    *,
    focus: tuple[int, Any] = (_OK, _FOCUSED),
    windows: list[Window] | None = None,
) -> FakeBridge:
    return FakeBridge(
        windows=windows,
        attrs={_APP_EL: {"AXFocusedUIElement": focus}, _FOCUSED: focused_attrs or {}},
    )


def _read(bridge: FakeBridge, *, max_chars: int = 8_000) -> SelectedText | ActionResult:
    return read_selected_text(7, "TextEdit", max_chars=max_chars, bridge=bridge)


def _failure(result: SelectedText | ActionResult) -> str:
    assert isinstance(result, ActionResult)
    assert result.success is False
    return result.output


@pytest.fixture(autouse=True)
def _on_darwin_as_pid_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tokenpal.desktop.selected_text.current_platform", lambda: "darwin")
    monkeypatch.setattr("tokenpal.desktop.selected_text.os.getpid", lambda: 1)


def test_selection_is_read_verbatim() -> None:
    result = _read(_bridge({"AXSelectedText": (_OK, FIXTURE)}))
    assert isinstance(result, SelectedText)
    assert result.content.text == FIXTURE
    assert result.content.source_app == "TextEdit"
    assert result.content.kind == "selection"
    assert result.whole_field is False
    assert result.truncated is False


def test_empty_selection_falls_back_to_the_whole_field() -> None:
    result = _read(_bridge({"AXSelectedText": (_OK, ""), "AXValue": (_OK, FIXTURE)}))
    assert isinstance(result, SelectedText)
    assert result.content.text == FIXTURE
    assert result.whole_field is True
    assert result.truncated is False


def test_whole_field_longer_than_the_cap_is_truncated_and_flagged() -> None:
    result = _read(_bridge({"AXValue": (_OK, "x" * 50)}), max_chars=20)
    assert isinstance(result, SelectedText)
    assert len(result.content.text) == 20
    assert result.truncated is True


def test_secure_field_is_refused_before_any_value_read() -> None:
    bridge = _bridge({
        "AXSubrole": (_OK, "AXSecureTextField"),
        "AXSelectedText": (_OK, FIXTURE),
        "AXValue": (_OK, FIXTURE),
    })
    assert _failure(_read(bridge)) == "Won't read a password field."
    assert not [name for _, name in bridge.reads if name in ("AXSelectedText", "AXValue")]


def test_api_disabled_names_the_process_holding_the_grant() -> None:
    output = _failure(_read(_bridge(focus=(_ERR_API_DISABLED, None))))
    assert "grant Accessibility" in output
    assert responsible_host() in output


def test_cannot_complete_reports_the_app_did_not_answer() -> None:
    output = _failure(_read(_bridge(focus=(_ERR_CANNOT_COMPLETE, None))))
    assert "TextEdit didn't answer" in output


def test_no_value_reports_nothing_focused() -> None:
    output = _failure(_read(_bridge(focus=(_NO_VALUE, None))))
    assert "Nothing is focused in TextEdit" in output
    assert "/proofread <text>" in output


def test_both_attributes_empty_reports_empty() -> None:
    output = _failure(_read(_bridge({"AXSelectedText": (_OK, ""), "AXValue": (_OK, "")})))
    assert "focused field in TextEdit is empty" in output


def test_non_string_attribute_values_are_treated_as_absent() -> None:
    output = _failure(_read(_bridge({"AXSelectedText": (_OK, (0, 12)), "AXValue": (_OK, 42)})))
    assert "is empty" in output


def test_the_timeout_is_set_on_both_elements_before_they_are_read() -> None:
    bridge = _bridge({"AXSelectedText": (_OK, FIXTURE)})
    _read(bridge)
    assert bridge.timeouts == [(_APP_EL, 2.0), (_FOCUSED, 2.0)]
    assert bridge.reads[0] == (_APP_EL, "AXFocusedUIElement")
    assert bridge.reads[1][0] == _FOCUSED


def test_source_app_under_a_terminal_skips_the_terminal() -> None:
    bridge = FakeBridge(windows=[_TERMINAL, (2, "Orca", "other tab", 0), _TEXTEDIT, _SAFARI])
    assert source_app(bridge) == (7, "TextEdit", "notes.txt")


def test_source_app_under_qt_is_the_frontmost_normal_window() -> None:
    """The Qt chat window floats above layer 0, so this process never leads
    the normal-window list; owning any on-screen window is the tell."""
    bridge = FakeBridge(windows=[_QT_CHAT, _TEXTEDIT, _SAFARI])
    assert source_app(bridge) == (7, "TextEdit", "notes.txt")


def test_source_app_under_qt_with_one_other_app() -> None:
    assert source_app(FakeBridge(windows=[_QT_CHAT, _TEXTEDIT])) == (7, "TextEdit", "notes.txt")


def test_source_app_ignores_floating_windows_of_other_apps() -> None:
    bridge = FakeBridge(windows=[(5, "BetterDisplay", "", 25), _TERMINAL, _TEXTEDIT])
    assert source_app(bridge) == (7, "TextEdit", "notes.txt")


def test_source_app_is_none_with_only_the_host() -> None:
    assert source_app(FakeBridge(windows=[_TERMINAL, (2, "Orca", "", 0)])) is None
    assert source_app(FakeBridge(windows=[_QT_CHAT])) is None
    assert source_app(FakeBridge(windows=[])) is None


def test_sensitive_source_app_is_refused_without_touching_the_bridge() -> None:
    bridge = _bridge(
        {"AXSelectedText": (_OK, FIXTURE)}, windows=[_TERMINAL, (5, "Messages", "Mom", 0)]
    )
    output = _failure(capture_selection(bridge=bridge))
    assert "sensitive-app list" in output
    assert "Messages" not in output
    assert bridge.reads == []


def test_sensitive_window_title_is_refused_without_touching_the_bridge() -> None:
    """A browser is never a sensitive app; the page it shows can be."""
    bridge = _bridge(
        {"AXSelectedText": (_OK, FIXTURE)},
        windows=[_TERMINAL, (9, "Safari", "Sign in - Venmo", 0)],
    )
    output = _failure(capture_selection(bridge=bridge))
    assert "sensitive-app list" in output
    assert "Venmo" not in output
    assert bridge.reads == []


def test_capture_selection_off_darwin_never_builds_a_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tokenpal.desktop.selected_text.current_platform", lambda: "windows")
    monkeypatch.setattr(
        "tokenpal.desktop.selected_text._MacAXBridge",
        lambda: pytest.fail("built a bridge off macOS"),
    )
    assert "only available on macOS" in _failure(capture_selection())


def test_capture_selection_reports_missing_pyobjc() -> None:
    class _NoPyObjC(FakeBridge):
        def windows(self) -> list[Window]:
            raise ImportError("No module named 'Quartz'")

    assert "pyobjc is not installed" in _failure(capture_selection(bridge=_NoPyObjC()))


def test_capture_selection_reports_no_source_app() -> None:
    output = _failure(capture_selection(bridge=FakeBridge(windows=[_TERMINAL])))
    assert "which app you came from" in output


def test_capture_selection_returns_the_read_failure() -> None:
    output = _failure(capture_selection(bridge=_bridge(focus=(_NO_VALUE, None))))
    assert "Nothing is focused in TextEdit" in output


def test_repr_omits_the_text() -> None:
    result = _read(_bridge({"AXSelectedText": (_OK, FIXTURE)}))
    assert FIXTURE not in repr(result)
    assert FIXTURE not in str(result)
    assert f"chars={len(FIXTURE)}" in repr(result)


def test_a_successful_read_leaks_the_text_into_no_sink(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="tokenpal.desktop.selected_text")
    result = capture_selection(bridge=_bridge({"AXSelectedText": (_OK, FIXTURE)}))
    assert isinstance(result, SelectedText)
    assert result.content.text == FIXTURE
    assert_no_leak(FIXTURE, lines=[], caplog_text=caplog.text)
