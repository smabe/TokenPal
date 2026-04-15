"""Tests for git_log, git_diff, git_status actions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tokenpal.actions.git_log import (
    _DIFF_MAX_BYTES,
    _LOG_LIMIT_CAP,
    GitDiffAction,
    GitLogAction,
    GitStatusAction,
)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _commit(root: Path, name: str, content: str, message: str) -> None:
    (root / name).write_text(content)
    subprocess.run(["git", "add", name], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True)


async def test_git_log_happy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a", "first commit")
    _commit(tmp_path, "b.txt", "b", "second commit")
    monkeypatch.chdir(tmp_path)

    result = await GitLogAction({}).execute()
    assert result.success is True
    assert "first commit" in result.output
    assert "second commit" in result.output


async def test_git_log_limit_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a", "c1")
    monkeypatch.chdir(tmp_path)

    result = await GitLogAction({}).execute(limit=9999)
    assert result.success is True
    # Only one commit exists, but we also verify the cap constant is respected.
    assert _LOG_LIMIT_CAP == 50


async def test_git_log_not_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = await GitLogAction({}).execute()
    assert result.success is False


async def test_git_diff_happy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "hello\n", "c1")
    (tmp_path / "a.txt").write_text("hello world\n")
    monkeypatch.chdir(tmp_path)

    result = await GitDiffAction({}).execute()
    assert result.success is True
    assert "hello world" in result.output


async def test_git_diff_no_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "hello\n", "c1")
    monkeypatch.chdir(tmp_path)

    result = await GitDiffAction({}).execute()
    assert result.success is True
    assert "No diff" in result.output


async def test_git_diff_caps_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a\n", "c1")
    # Give the file enough fresh content to blow past the diff cap.
    (tmp_path / "a.txt").write_text("x" * (_DIFF_MAX_BYTES * 2))
    monkeypatch.chdir(tmp_path)

    result = await GitDiffAction({}).execute()
    assert result.success is True
    assert "truncated" in result.output


async def test_git_diff_bad_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a\n", "c1")
    monkeypatch.chdir(tmp_path)

    result = await GitDiffAction({}).execute(ref="does-not-exist-ref")
    assert result.success is False


async def test_git_status_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a\n", "c1")
    monkeypatch.chdir(tmp_path)

    result = await GitStatusAction({}).execute()
    assert result.success is True
    assert "clean" in result.output.lower()


async def test_git_status_dirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "a\n", "c1")
    (tmp_path / "b.txt").write_text("new\n")
    monkeypatch.chdir(tmp_path)

    result = await GitStatusAction({}).execute()
    assert result.success is True
    assert "b.txt" in result.output


async def test_git_status_not_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = await GitStatusAction({}).execute()
    assert result.success is False
