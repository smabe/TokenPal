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


def _repo_with_escaping_symlink(tmp_path: Path) -> tuple[Path, Path]:
    """Repo containing a git-tracked symlink whose target lives outside it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("PRIVATE KEY MATERIAL\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    link = repo / "notes.txt"
    link.symlink_to(Path("..") / "outside" / "private.txt")
    subprocess.run(["git", "add", "notes.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo, link


async def test_read_file_rejects_tracked_symlink_escaping_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _repo_with_escaping_symlink(tmp_path)
    monkeypatch.chdir(repo)

    result = await ReadFileAction({}).execute(path="notes.txt")
    assert result.success is False
    assert result.output == "Path is outside the git repo."
    assert "PRIVATE KEY MATERIAL" not in result.output


async def test_read_file_absolute_and_relative_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, link = _repo_with_escaping_symlink(tmp_path)
    monkeypatch.chdir(repo)

    relative = await ReadFileAction({}).execute(path="notes.txt")
    absolute = await ReadFileAction({}).execute(path=str(link))
    assert (relative.success, relative.output) == (absolute.success, absolute.output)


async def test_read_file_refusal_does_not_echo_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _repo_with_escaping_symlink(tmp_path)
    monkeypatch.chdir(repo)

    result = await ReadFileAction({}).execute(path="notes.txt")
    assert "private.txt" not in result.output
    assert str(tmp_path) not in result.output


def _repo_with_internal_symlink(root: Path, *, track_link: bool) -> Path:
    """Repo holding a tracked, denylisted secret plus a symlink aliasing it."""
    _init_repo(root)
    (root / "docs").mkdir()
    (root / "docs" / "credentials.md").write_text("AWS_SECRET=abc123\n")
    (root / "notes.md").symlink_to(Path("docs") / "credentials.md")
    subprocess.run(["git", "add", "docs/credentials.md"], cwd=root, check=True)
    if track_link:
        subprocess.run(["git", "add", "notes.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


async def test_tracked_symlink_cannot_launder_a_denied_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_internal_symlink(tmp_path, track_link=True)
    monkeypatch.chdir(repo)

    direct = await ReadFileAction({}).execute(path="docs/credentials.md")
    assert direct.success is False

    aliased = await ReadFileAction({}).execute(path="notes.md")
    assert aliased.success is False
    assert "AWS_SECRET" not in aliased.output


async def test_untracked_symlink_cannot_read_a_tracked_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_internal_symlink(tmp_path, track_link=False)
    monkeypatch.chdir(repo)

    result = await ReadFileAction({}).execute(path="notes.md")
    assert result.success is False
    assert "AWS_SECRET" not in result.output


async def test_unreadable_target_refusal_does_not_echo_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.txt").write_text("hi\n")
    subprocess.run(["git", "add", "docs/note.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    result = await ReadFileAction({}).execute(path="docs")
    assert result.success is False
    assert str(tmp_path) not in result.output


async def test_dash_leading_path_is_not_parsed_as_a_git_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "kept.txt").write_text("kept\n")
    subprocess.run(["git", "add", "kept.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / "--others").write_text("untracked payload\n")
    monkeypatch.chdir(tmp_path)

    result = await ReadFileAction({}).execute(path="--others")
    assert result.success is False
    assert "untracked payload" not in result.output
