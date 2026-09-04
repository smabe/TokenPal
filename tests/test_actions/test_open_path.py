"""Tests for the open_path action.

This is the boundary that actually launches something, so every refusal case
asserts on the launcher mock's call list — a refusal string alone would not
prove that ``open`` never ran.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests._helpers import stub_allowed_root
from tokenpal.actions import open_path as open_path_mod
from tokenpal.actions.catalog import LOCAL_SECTION
from tokenpal.actions.open_path import OpenPathAction
from tokenpal.config.schema import DEFAULT_TOOLS


class _Launcher:
    """Stands in for ``subprocess.Popen`` and ``os.startfile``."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.raises: BaseException | None = None

    def popen(self, argv: list[str], **_kwargs: Any) -> object:
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return object()

    def startfile(self, target: str) -> None:
        self.calls.append(target)
        if self.raises is not None:
            raise self.raises


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> _Launcher:
    fake = _Launcher()
    monkeypatch.setattr(open_path_mod.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(os, "startfile", fake.startfile, raising=False)
    monkeypatch.setattr(open_path_mod, "current_platform", lambda: "darwin")
    return fake


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An allowed root with HOME pointed at it, and no git root appended."""
    allowed = tmp_path / "root"
    allowed.mkdir()
    stub_allowed_root(monkeypatch, open_path_mod, allowed)
    return allowed


def _write(path: Path, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    path.chmod(mode)
    return path


# --- catalog / registration ---


def test_open_path_is_catalogued_and_opt_in() -> None:
    entry = next(e for e in LOCAL_SECTION.entries if e.name == "open_path")
    assert entry.consent_category == ""
    assert "open_path" not in DEFAULT_TOOLS


def test_open_path_requires_confirm() -> None:
    assert OpenPathAction.requires_confirm is True
    assert OpenPathAction.safe is False
    assert OpenPathAction.cacheable is False


# --- happy path ---


@pytest.mark.parametrize(
    "name", ["a.pdf", "notes.txt", "page.html", "README", "notes.unknownext"]
)
async def test_openable_shapes_open(root: Path, launcher: _Launcher, name: str) -> None:
    target = _write(root / name)

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is True, result.output
    assert launcher.calls == [["open", str(target)]]


# --- containment ---


async def test_refuses_a_path_outside_the_roots(
    root: Path, tmp_path: Path, launcher: _Launcher
) -> None:
    outside = _write(tmp_path / "outside" / "a.pdf")

    result = await OpenPathAction({}).execute(path=str(outside))

    assert result.success is False
    assert "outside" in result.output
    assert launcher.calls == []


async def test_refuses_a_symlink_that_resolves_outside(
    root: Path, tmp_path: Path, launcher: _Launcher
) -> None:
    outside = _write(tmp_path / "outside" / "a.pdf")
    link = root / "inside.pdf"
    link.symlink_to(outside)

    result = await OpenPathAction({}).execute(path=str(link))

    assert result.success is False
    assert launcher.calls == []


async def test_refuses_a_dotdot_escape(
    root: Path, tmp_path: Path, launcher: _Launcher
) -> None:
    _write(tmp_path / "outside" / "a.pdf")

    result = await OpenPathAction({}).execute(path=str(root / ".." / "outside" / "a.pdf"))

    assert result.success is False
    assert launcher.calls == []


async def test_refuses_when_allowed_dirs_is_empty(
    root: Path, launcher: _Launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write(root / "a.pdf")
    cfg = SimpleNamespace(paths=SimpleNamespace(allowed_dirs=[]))
    monkeypatch.setattr(open_path_mod, "load_config", lambda: cfg)

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False
    assert "[paths] allowed_dirs" in result.output
    assert launcher.calls == []


# --- shape of the target ---


async def test_refuses_a_missing_file(root: Path, launcher: _Launcher) -> None:
    result = await OpenPathAction({}).execute(path=str(root / "gone.pdf"))

    assert result.success is False
    assert launcher.calls == []


async def test_refuses_a_directory(root: Path, launcher: _Launcher) -> None:
    (root / "sub").mkdir()

    result = await OpenPathAction({}).execute(path=str(root / "sub"))

    assert result.success is False
    assert "folders" in result.output
    assert launcher.calls == []


@pytest.mark.parametrize("raw", ["", "   ", None])
async def test_refuses_a_missing_argument(
    root: Path, launcher: _Launcher, raw: str | None
) -> None:
    result = await OpenPathAction({}).execute(path=raw)

    assert result.success is False
    assert launcher.calls == []


# --- executables, scripts, bundles ---


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("run.sh", 0o644),
        ("run.command", 0o644),
        ("x.app/Contents/Resources/icon.png", 0o644),
        ("tool.py", 0o644),
        ("setup.exe", 0o644),
        ("a.lnk", 0o644),
        ("b.vbs", 0o644),
        ("c.jar", 0o644),
        ("d.pkg", 0o644),
        ("e.dmg", 0o644),
        ("f.webloc", 0o644),
        ("bare", 0o755),
        ("notes.txt", 0o755),
    ],
)
async def test_refuses_anything_that_could_run(
    root: Path, launcher: _Launcher, name: str, mode: int
) -> None:
    target = _write(root / name, mode)

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False, f"{name} was opened"
    assert launcher.calls == [], f"{name} reached the launcher"


async def test_type_checks_read_the_resolved_target_not_the_argument(
    root: Path, launcher: _Launcher
) -> None:
    """A benign-looking name pointing at a script must be refused as a script."""
    script = _write(root / "hidden" / "payload.sh")
    link = root / "notes.txt"
    link.symlink_to(script)

    result = await OpenPathAction({}).execute(path=str(link))

    assert result.success is False
    assert launcher.calls == []


# --- protected and sensitive ---


async def test_refuses_a_sensitive_name_without_repeating_it(
    root: Path, launcher: _Launcher
) -> None:
    target = _write(root / "1password-export.pdf")

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False
    assert "1password" not in result.output.lower()
    assert str(target) not in result.output
    assert launcher.calls == []


@pytest.mark.parametrize(
    "name",
    [".hidden/a.pdf", "Library/a.pdf", "x.env", "credentials.json", "id_rsa.txt"],
)
async def test_refuses_protected_paths(
    root: Path, launcher: _Launcher, name: str
) -> None:
    target = _write(root / name)

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False, f"{name} was opened"
    assert launcher.calls == []


# --- other platforms ---


async def test_windows_uses_startfile(
    root: Path, launcher: _Launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(open_path_mod, "current_platform", lambda: "windows")
    target = _write(root / "a.pdf")

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is True
    assert launcher.calls == [str(target)]


async def test_windows_launch_failure_refuses(
    root: Path, launcher: _Launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(open_path_mod, "current_platform", lambda: "windows")
    launcher.raises = OSError("no association")
    target = _write(root / "a.pdf")

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False
    assert "could not open" in result.output


async def test_linux_without_xdg_open_refuses(
    root: Path, launcher: _Launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(open_path_mod, "current_platform", lambda: "linux")
    launcher.raises = FileNotFoundError("xdg-open")
    target = _write(root / "a.pdf")

    result = await OpenPathAction({}).execute(path=str(target))

    assert result.success is False
    assert "xdg-open" in result.output
    assert launcher.calls == [["xdg-open", str(target)]]
