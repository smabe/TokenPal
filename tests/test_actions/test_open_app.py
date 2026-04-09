"""Tests for the open_app action."""

from __future__ import annotations

from unittest.mock import patch

from tokenpal.actions.open_app import OpenAppAction


def _make_open_app():
    return OpenAppAction({})


async def test_open_app_rejects_unknown():
    action = _make_open_app()
    result = await action.execute(app_name="malware.exe")
    assert result.success is False
    assert "not in the allowed list" in result.output


async def test_open_app_rejects_empty():
    action = _make_open_app()
    result = await action.execute(app_name="")
    assert result.success is False


async def test_open_app_case_insensitive_allowlist():
    action = _make_open_app()
    r = await action.execute(app_name="HACKER_TOOL")
    assert r.success is False


@patch("tokenpal.actions.open_app.subprocess.Popen")
async def test_open_app_allowed_launches(mock_popen):
    action = _make_open_app()
    result = await action.execute(app_name="Calculator")
    assert result.success is True
    assert "Opening" in result.output
    mock_popen.assert_called_once()


@patch("tokenpal.actions.open_app.subprocess.Popen", side_effect=OSError("not found"))
async def test_open_app_handles_oserror(mock_popen):
    action = _make_open_app()
    result = await action.execute(app_name="Calculator")
    assert result.success is False
    assert "Failed" in result.output
