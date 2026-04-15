"""Tests for the read_file action."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tokenpal.actions.read_file import _MAX_BYTES, ReadFileAction


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


async def test_read_file_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    f = tmp_path / "hello.txt"
    f.write_text("hello world\n")
    subprocess.run(["git", "add", "hello.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    result = await ReadFileAction({}).execute(path="hello.txt")
    assert result.success is True
    assert "hello world" in result.output


async def test_read_file_rejects_untracked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("secret")
    monkeypatch.chdir(tmp_path)

    result = await ReadFileAction({}).execute(path="untracked.txt")
    assert result.success is False
    assert "not tracked" in result.output.lower()


async def test_read_file_rejects_denied_pattern() -> None:
    for bad in [".env", "src/credentials.json", "key.pem", "api.key", "deploy/secrets.yml"]:
        result = await ReadFileAction({}).execute(path=bad)
        assert result.success is False


async def test_read_file_caps_at_max_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (_MAX_BYTES + 1024))
    subprocess.run(["git", "add", "big.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    result = await ReadFileAction({}).execute(path="big.txt")
    assert result.success is True
    assert "truncated" in result.output
    # Output is text content plus the marker — truncated body equals MAX_BYTES of 'x'.
    assert result.output.count("x") == _MAX_BYTES


async def test_read_file_missing_path() -> None:
    result = await ReadFileAction({}).execute(path="")
    assert result.success is False


async def test_read_file_outside_repo_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = await ReadFileAction({}).execute(path="anything.txt")
    assert result.success is False
