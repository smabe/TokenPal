"""--validate rows for the desktop-content OS grants.

Asserts the rendered rows only, never the header: the header carries
``sys.executable``, which differs per host. The probes are patched so the
rows are asserted independently of this machine's real TCC state.
"""

from __future__ import annotations

import sys
from unittest import mock

from tokenpal.cli import _check_desktop_permissions
from tokenpal.config.schema import TokenPalConfig

_CONFIG = TokenPalConfig()


def _rows(capsys) -> str:
    out = capsys.readouterr().out
    return "\n".join(
        line for line in out.splitlines() if "permissions checked for" not in line
    )


def test_darwin_reports_both_grants_present(capsys) -> None:
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=True,
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=True,
    ):
        _check_desktop_permissions("Darwin", as_bundle=False)
    rows = _rows(capsys)
    assert "Accessibility: granted" in rows
    assert "Screen Recording: granted" in rows
    assert "missing" not in rows


def test_darwin_points_at_settings_when_grants_missing(capsys) -> None:
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=False,
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=False,
    ):
        _check_desktop_permissions("Darwin", as_bundle=False)
    rows = _rows(capsys)
    assert (
        "Accessibility: missing — System Settings > Privacy & Security > Accessibility"
        in rows
    )
    assert (
        "Screen Recording: missing — System Settings > Privacy & Security > "
        "Screen & System Audio Recording" in rows
    )


def test_darwin_reports_unknown_when_pyobjc_is_absent(capsys) -> None:
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=None,
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=None,
    ):
        _check_desktop_permissions("Darwin", as_bundle=False)
    rows = _rows(capsys)
    assert "Accessibility: unknown (pyobjc unavailable)" in rows
    assert "Screen Recording: unknown (pyobjc unavailable)" in rows


def test_non_darwin_reports_a_single_no_grants_row(capsys) -> None:
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted",
        side_effect=AssertionError("probed off macOS"),
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted",
        side_effect=AssertionError("probed off macOS"),
    ):
        _check_desktop_permissions("Windows", as_bundle=False)
    rows = _rows(capsys)
    assert "no OS permission grants needed" in rows
    assert "Accessibility" not in rows
    assert "Screen Recording" not in rows


def test_darwin_header_names_the_responsible_process(capsys, monkeypatch) -> None:
    """macOS attributes these grants to the parent terminal, not the
    interpreter — naming python sends the user hunting in the wrong place.
    Mirrors the microphone row in _check_audio."""
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=True
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=True
    ), mock.patch(
        "tokenpal.desktop.permissions.platform.system", return_value="Darwin"
    ):
        _check_desktop_permissions("Darwin", as_bundle=False)
    header = capsys.readouterr().out.splitlines()[1]
    assert "iTerm.app" in header
    assert sys.executable not in header


def test_darwin_header_falls_back_when_term_program_is_unset(
    capsys, monkeypatch
) -> None:
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=True
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=True
    ), mock.patch(
        "tokenpal.desktop.permissions.platform.system", return_value="Darwin"
    ):
        _check_desktop_permissions("Darwin", as_bundle=False)
    assert sys.executable in capsys.readouterr().out.splitlines()[1]


def test_darwin_header_names_tokenpal_when_the_buddy_runs_as_the_bundle(
    capsys, monkeypatch
) -> None:
    """On the Qt path the process that holds the grants is TokenPal.app, not
    the terminal — and the probed rows still describe the terminal, so the
    user has to be told the rows and the grant are about different apps."""
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    with mock.patch(
        "tokenpal.desktop.permissions.accessibility_granted", return_value=True
    ), mock.patch(
        "tokenpal.desktop.permissions.screen_recording_granted", return_value=True
    ):
        _check_desktop_permissions("Darwin", as_bundle=True)
    out = capsys.readouterr().out
    assert "TokenPal" in out.splitlines()[1]
    assert "iTerm.app" not in out.splitlines()[1]
    assert "report this terminal, not TokenPal" in out
