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

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            # Patching create_subprocess_exec on the asyncio module hits every
            # importer, so this also answers `git rev-parse --show-toplevel`
            # and `git check-ignore`. A blank answer refuses now instead of
            # falling back to the cwd.
            return str(tmp_path).encode(), b""

    async def fake_exec(*argv: str, **_kwargs: object) -> _Proc:
        captured.append(list(argv))
        return _Proc()

    monkeypatch.setattr(
        "tokenpal.util.proc.asyncio.create_subprocess_exec", fake_exec
    )

    await invoke_tool(GrepCodebaseAction({}), pattern="MARKER")

    # Several git calls surround it, so pick the rg argv by name rather than
    # by position: check-ignore now runs after it.
    rg_argv = next(a for a in captured if any(x.endswith("rg") for x in a[:1]))
    caps = [a for a in rg_argv if a.startswith("--max-count") or a == "-m"]
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


# --- the output screen (each hit, not just the target) ---


async def test_secret_named_files_are_not_returned_without_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The target is the repo root, which no screen refuses -- so the hits are
    the only thing between the model and a key file's contents."""
    _init_repo(tmp_path)
    (tmp_path / "credentials.md").write_text("MARKERSECRET=abc\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "id_rsa").write_text("MARKERSECRET=ghi\n")
    (tmp_path / "ok.txt").write_text("MARKERSECRET is here\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET")

    assert result.success is True
    assert "credentials.md" not in result.output
    assert "id_rsa" not in result.output
    assert "ok.txt" in result.output


@pytest.mark.parametrize("named", [".git", ".aws", "ignored"])
async def test_a_hidden_or_ignored_target_returns_none_of_its_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str, named: str
) -> None:
    """rg applies neither its hidden-file nor its gitignore filter to a path
    named on the command line, and the invoker screens the target's own name,
    not its contents."""
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / ".git" / "description").write_text("MARKERSECRET=xyz\n")
    for folder, name in ((".aws", "credentials"), ("ignored", "prod.env")):
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / name).write_text("MARKERSECRET=abc\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET", path=named)

    assert "MARKERSECRET" not in result.output


async def test_a_path_containing_a_colon_still_returns_its_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The screen splits rg's path from its match on a NUL, not on the colon
    that also separates them, so a colon in a filename is not a truncation."""
    _init_repo(tmp_path)
    (tmp_path / "we:ird.txt").write_text("needle here\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="needle")

    assert result.success is True
    assert "we:ird.txt:1:needle here" in result.output


async def test_a_single_file_target_keeps_its_hit_and_its_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """rg prints no path when the target is one file, so the hit is attributed
    to the target -- and a hidden one is still screened."""
    _init_repo(tmp_path)
    (tmp_path / "ok.txt").write_text("needle here\n")
    (tmp_path / ".git" / "description").write_text("needle there\n")
    monkeypatch.chdir(tmp_path)

    plain = await invoke_tool(GrepCodebaseAction({}), pattern="needle", path="ok.txt")
    hidden = await invoke_tool(
        GrepCodebaseAction({}), pattern="needle", path=".git/description"
    )

    assert plain.output == "1:needle here"
    assert "needle" not in hidden.output


async def test_a_gitignored_file_with_a_benign_name_is_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The sibling case used `prod.env`, which REJECT_PATH catches by name.

    ripgrep honours .gitignore while it walks but not for a path named on its
    command line, and the name screen cannot see an ignore rule -- so a benign
    name in an ignored folder is the case that needs git asked.
    """
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "notes.txt").write_text("MARKERSECRET benign name\n")
    (tmp_path / "debug.log").write_text("MARKERSECRET in a log\n")
    monkeypatch.chdir(tmp_path)

    for named in ("ignored", "debug.log"):
        result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET", path=named)
        assert "MARKERSECRET" not in result.output, f"{named} leaked an ignored file"


async def test_a_record_split_by_a_form_feed_cannot_escape_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """`str.splitlines()` breaks on eight bytes beyond \n.

    A form feed is ordinary in source files. Splitting on them turns one rg
    record into two, and the tail carries no path -- so it used to inherit the
    TARGET's verdict and escape its own file's screen.
    """
    _init_repo(tmp_path)
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "id_rsa").write_text("MARKERSECRET_HEAD \x0b TAIL_MUST_NOT_LEAK\n")
    (tmp_path / "ok" / "notes.txt").write_text("MARKERSECRET_FINE\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET", path="ok")

    assert "TAIL_MUST_NOT_LEAK" not in result.output
    assert "MARKERSECRET_FINE" in result.output


async def test_a_filename_containing_a_newline_is_dropped_not_reparsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """The fragment after the newline is not a path rg emitted.

    Reparsing it resolved against the cwd rather than the target, which let a
    file inside a fully screened directory come back.
    """
    _init_repo(tmp_path)
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "we\nird.txt").write_text("MARKERSECRET_NEWLINE\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET", path=".aws")

    assert "MARKERSECRET_NEWLINE" not in result.output


async def test_a_gitignored_file_git_would_quote_is_still_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """`git check-ignore` C-quotes a path holding a non-ASCII byte or a quote.

    Comparing git's quoted output to ripgrep's raw path never matches, so the
    screen failed OPEN on exactly the names hardest to eyeball. `-z --stdin`
    is what makes git emit them verbatim.
    """
    _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "ignored").mkdir()
    for name in ("plain.txt", "caf\u00e9.txt", 'qu"ote.txt'):
        (tmp_path / "ignored" / name).write_text("MARKERSECRET\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET", path="ignored")

    assert "MARKERSECRET" not in result.output


async def test_a_form_feed_does_not_truncate_a_returned_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, require_rg: str
) -> None:
    """Pins `split("\n")` over `splitlines()`, which breaks on eight more bytes.

    With the sep-less drop in place a revert here truncates rather than leaks,
    but the drop is the only thing standing between it and the original hole.
    """
    _init_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("MARKERSECRET\x0ctail_must_survive\n")
    monkeypatch.chdir(tmp_path)

    result = await invoke_tool(GrepCodebaseAction({}), pattern="MARKERSECRET")

    assert "tail_must_survive" in result.output
