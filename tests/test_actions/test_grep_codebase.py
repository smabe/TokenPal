"""Tests for the grep_codebase action."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tokenpal.actions.grep_codebase import GrepCodebaseAction


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


@pytest.fixture
def require_rg() -> str:
    rg = shutil.which("rg")
    if rg is None:
        pytest.skip("ripgrep not installed")
    return rg


async def test_grep_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("needle lives here\nno match\n")
    monkeypatch.chdir(tmp_path)

    result = await GrepCodebaseAction({}).execute(pattern="needle")
    assert result.success is True
    assert "needle" in result.output


async def test_grep_missing_pattern() -> None:
    result = await GrepCodebaseAction({}).execute(pattern="")
    assert result.success is False


async def test_grep_no_rg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.grep_codebase.shutil.which", lambda _name: None
    )
    result = await GrepCodebaseAction({}).execute(pattern="anything")
    assert result.success is False
    assert "ripgrep" in result.output.lower()


async def test_grep_cap_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    _init_repo(tmp_path)
    # Write 200 files each with a single match so we blow past the 100 cap.
    for i in range(200):
        (tmp_path / f"f{i}.txt").write_text("MARKER here\n")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("tokenpal.actions.grep_codebase._MAX_MATCHES", 10)

    result = await GrepCodebaseAction({}).execute(pattern="MARKER")
    assert result.success is True
    assert "capped" in result.output
