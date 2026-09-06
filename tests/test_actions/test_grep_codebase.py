"""Tests for the grep_codebase action."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._helpers import invoke_tool
from tokenpal.actions.grep_codebase import _MAX_PER_FILE, GrepCodebaseAction


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

    result = await invoke_tool(GrepCodebaseAction({}), pattern="needle")
    assert result.success is True
    assert "needle" in result.output


async def test_grep_missing_pattern() -> None:
    result = await invoke_tool(GrepCodebaseAction({}), pattern="")
    assert result.success is False


async def test_grep_no_rg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tokenpal.actions.grep_codebase.shutil.which", lambda _name: None
    )
    result = await invoke_tool(GrepCodebaseAction({}), pattern="anything")
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

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKER")
    assert result.success is True
    assert "capped" in result.output


async def test_argv_carries_one_per_file_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """Two spellings of --max-count meant the later one (100) silently won,
    letting a single noisy file eat the whole 100-line budget."""
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            # Patching create_subprocess_exec on the asyncio module hits every
            # importer, so this also answers `git rev-parse --show-toplevel`.
            # A blank answer refuses now instead of falling back to the cwd.
            return str(tmp_path).encode(), b""

    async def fake_exec(*argv: str, **_kwargs: object) -> _Proc:
        captured.append(list(argv))
        return _Proc()

    monkeypatch.setattr(
        "tokenpal.util.proc.asyncio.create_subprocess_exec", fake_exec
    )

    await invoke_tool(GrepCodebaseAction({}), pattern="MARKER")

    # git_root shells out first; the rg argv is the last one captured.
    caps = [a for a in captured[-1] if a.startswith("--max-count") or a == "-m"]
    assert caps == [f"--max-count={_MAX_PER_FILE}"]


async def test_exact_cap_is_not_labelled_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("tokenpal.actions.grep_codebase._MAX_MATCHES", 3)
    # Three files so the per-file cap of 5 never bites: exactly _MAX_MATCHES
    # lines survive and nothing was dropped.
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("MARKER here\n")

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKER")

    assert result.success is True
    assert len(result.output.splitlines()) == 3
    assert "capped" not in result.output


# --- containment (the invoker's, on a declared path) ---


async def test_grep_refuses_an_absolute_path_outside_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.txt").write_text("MARKER outside\n")
    monkeypatch.chdir(repo)

    result = await invoke_tool(
        GrepCodebaseAction({}), pattern="MARKER", path=str(outside)
    )

    assert result.success is False
    assert "MARKER" not in result.output
    assert str(tmp_path) not in result.output


async def test_grep_refuses_a_dotdot_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "a.txt").write_text("MARKER outside\n")
    monkeypatch.chdir(repo)

    result = await invoke_tool(
        GrepCodebaseAction({}), pattern="MARKER", path="../outside"
    )

    assert result.success is False
    assert "MARKER" not in result.output


async def test_grep_refuses_a_symlink_resolving_outside_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The target reaches an argv, so validating the argument is not enough --
    the resolved value is what rg walks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("MARKER outside\n")
    (repo / "inside").symlink_to(outside)
    monkeypatch.chdir(repo)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKER", path="inside")

    assert result.success is False
    assert "MARKER" not in result.output


async def test_grep_searches_the_resolved_target_not_the_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("MARKER inside\n")
    (tmp_path / "elsewhere.txt").write_text("MARKER elsewhere\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKER", path="sub")

    assert result.success is True
    assert "elsewhere.txt" not in result.output
    assert "a.txt" in result.output


async def test_grep_refuses_a_denied_raw_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The broad screen it already applied, plus REJECT_PATH it did not."""
    _init_repo(tmp_path)
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "a.txt").write_text("MARKER\n")
    monkeypatch.chdir(tmp_path)

    denied = await invoke_tool(
        GrepCodebaseAction({}), pattern="MARKER", path="credentials"
    )
    sensitive = await invoke_tool(
        GrepCodebaseAction({}), pattern="MARKER", path="1password"
    )

    assert denied.success is False
    assert sensitive.success is False


async def test_grep_without_a_path_refuses_outside_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """"Search the current repo" has no meaning outside one; the old cwd
    fallback searched whatever folder the buddy happened to launch from."""
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKER")

    assert result.success is False
    assert "git" in result.output.lower()


@pytest.mark.parametrize("bad", [123, ["sub"], True, {"a": 1}])
async def test_an_argument_the_invoker_declined_to_contain_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str, bad: object
) -> None:
    """A non-str `path` skips containment, so the tool must refuse it itself.

    The old code fell through to `target = root` and searched the whole repo;
    a model emitting a JSON number is the realistic route in.
    """
    _init_repo(tmp_path)
    (tmp_path / "hit.txt").write_text("needle\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="needle", path=bad)

    assert result.success is False
    assert "needle" not in result.output
