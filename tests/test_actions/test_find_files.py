"""Tests for the find_files action.

The post-filter is a security boundary: every case that asserts a path is
*absent* is asserting the LLM never learns that filename.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests._helpers import stub_allowed_root
from tokenpal.actions import find_files
from tokenpal.actions.catalog import LOCAL_SECTION
from tokenpal.actions.find_files import FindFilesAction
from tokenpal.config.schema import DEFAULT_TOOLS
from tokenpal.util import proc as proc_mod


class _FakeProc:
    def __init__(self, stdout: bytes) -> None:
        self._stdout = stdout
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return 0


def _fake_exec(stdout: bytes, captured: list[list[str]]) -> Any:
    async def run(*argv: str, **_kwargs: Any) -> _FakeProc:
        captured.append(list(argv))
        return _FakeProc(stdout)

    return run


def _build_tree(tmp_path: Path) -> Path:
    """Allowed root holding one legitimate hit plus one of every denied shape."""
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "Library" / "Preferences").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    files = {
        root / "doc-a.pdf": 500,
        root / "sub" / "doc-b.txt": 400,
        root / ".hidden" / "doc-c.pdf": 300,
        root / "Library" / "Preferences" / "doc-d.pdf": 300,
        root / "doc.env": 300,
        root / "doc-credentials.json": 300,
        root / "1password-doc.csv": 300,
        root / "doc-id_rsa.txt": 300,
        outside / "doc-outside.pdf": 300,
    }
    now = 1_700_000_000
    for path, offset in files.items():
        path.write_text("x")
        os.utime(path, (now + offset, now + offset))

    (root / "doc-link.pdf").symlink_to(outside / "doc-outside.pdf")
    return root


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tree under an allowed root, with HOME pointed at that root.

    HOME matters: ``is_hidden_or_protected`` special-cases ``~/Library``, and
    pointing HOME at the root exercises that rule alongside the generic one.
    """
    root = _build_tree(tmp_path)
    stub_allowed_root(monkeypatch, root)
    return root


# --- predicate builder / injection regression ---


async def test_spotlight_argv_escapes_the_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", _fake_exec(b"", captured))
    roots = [tmp_path / "one", tmp_path / "two"]

    await find_files._run_backend("darwin", roots, 'a"b\\c', "pdf", 172_800, 50)

    assert captured == [
        [
            "mdfind",
            "-0",
            "-onlyin",
            str(roots[0]),
            "-onlyin",
            str(roots[1]),
            '(kMDItemFSName == "*a\\"b\\\\c*"cd || kMDItemTextContent == "a\\"b\\\\c*"cd)'
            ' && (kMDItemContentTypeTree == "com.adobe.pdf")'
            " && (kMDItemContentModificationDate >= $time.now(-172800))",
        ]
    ]


# --- modified_within parser ---


@pytest.mark.parametrize(
    ("raw", "seconds"), [("12h", 43_200), ("2d", 172_800), ("1w", 604_800)]
)
def test_modified_within_parses(raw: str, seconds: int) -> None:
    assert find_files._parse_within(raw) == seconds


@pytest.mark.parametrize("raw", ["2x", "-1d", "", "0d", "2 d", "d"])
async def test_modified_within_refuses_garbage(raw: str, sandbox: Path) -> None:
    result = await FindFilesAction({}).execute(query="doc", modified_within=raw)
    assert result.success is False
    assert "modified_within" in result.output


# --- limit ---


async def test_limit_zero_is_refused(sandbox: Path) -> None:
    result = await FindFilesAction({}).execute(query="doc", limit=0)
    assert result.success is False
    assert "limit" in result.output


async def test_limit_is_clamped_to_max(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = find_files._post_filter
    monkeypatch.setattr(
        find_files,
        "_post_filter",
        lambda candidates, roots, kind, limit, screen: (
            seen.append(limit),
            real(candidates, roots, kind, limit, screen),
        )[1],
    )
    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")

    await FindFilesAction({}).execute(query="doc", limit=500)
    assert seen == [50]


# --- walk backend over a real tree ---


async def test_walk_returns_only_allowed_files_newest_first(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")

    result = await FindFilesAction({}).execute(query="doc")

    assert result.success is True
    paths = [line.split("  ", 1)[1] for line in result.output.splitlines()]
    assert paths == [str(sandbox / "doc-a.pdf"), str(sandbox / "sub" / "doc-b.txt")]


# --- Spotlight results run through the same post-filter ---


async def test_spotlight_results_are_post_filtered(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every denied shape, handed straight to the filter with no walk pruning."""
    monkeypatch.setattr(find_files, "current_platform", lambda: "darwin")
    denied = [
        sandbox / ".hidden" / "doc-c.pdf",
        sandbox / "Library" / "Preferences" / "doc-d.pdf",
        sandbox / "doc.env",
        sandbox / "doc-credentials.json",
        sandbox / "1password-doc.csv",
        sandbox / "doc-id_rsa.txt",
        sandbox / "doc-link.pdf",
        sandbox.parent / "outside" / "doc-outside.pdf",
    ]
    allowed = [sandbox / "doc-a.pdf", sandbox / "sub" / "doc-b.txt"]
    for path in denied:
        assert path.exists(), path
    stdout = "\0".join(str(p) for p in [*denied, *allowed]).encode()
    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", _fake_exec(stdout, []))

    result = await FindFilesAction({}).execute(query="doc")

    paths = [line.split("  ", 1)[1] for line in result.output.splitlines()]
    assert paths == [str(allowed[0]), str(allowed[1])]
    for path in denied:
        assert path.name not in result.output


async def test_spotlight_timeout_is_a_refusal(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "darwin")

    class _HangingProc(_FakeProc):
        async def communicate(self) -> tuple[bytes, bytes]:
            raise TimeoutError

    async def run(*_argv: str, **_kwargs: Any) -> _HangingProc:
        return _HangingProc(b"")

    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", run)

    result = await FindFilesAction({}).execute(query="doc")
    assert result.success is False
    assert "timed out" in result.output


async def test_missing_mdfind_falls_back_to_the_walk(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "darwin")

    async def missing(*_argv: str, **_kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("mdfind")

    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", missing)

    result = await FindFilesAction({}).execute(query="doc")
    assert str(sandbox / "doc-a.pdf") in result.output


# --- walk bounds ---


async def test_walk_stops_past_the_depth_cap(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")
    deep = sandbox.joinpath(*[f"d{i}" for i in range(10)])
    deep.mkdir(parents=True)
    (deep / "doc-deep.pdf").write_text("x")
    shallow = sandbox / "d0" / "d1" / "doc-shallow.pdf"
    shallow.write_text("x")

    result = await FindFilesAction({}).execute(query="doc")

    assert "doc-deep.pdf" not in result.output
    assert str(shallow) in result.output


async def test_directory_symlink_loop_terminates(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")
    (sandbox / "loop").symlink_to(sandbox, target_is_directory=True)

    result = await FindFilesAction({}).execute(query="doc")

    assert result.success is True
    assert f"{os.sep}loop{os.sep}" not in result.output


# --- registration ---


def test_find_files_is_opt_in_and_catalogued() -> None:
    assert "find_files" not in DEFAULT_TOOLS
    assert "find_files" in {entry.name for entry in LOCAL_SECTION.entries}


async def test_bare_wildcard_query_is_refused(sandbox: Path) -> None:
    result = await FindFilesAction({}).execute(query="**")
    assert result.success is False
    assert "query" in result.output


async def test_no_matches_names_the_root_count(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")
    result = await FindFilesAction({}).execute(query="nothingmatchesthis")
    assert result.output == "No matches under 1 allowed folders."


def test_find_files_never_opens_a_file() -> None:
    source = Path(find_files.__file__).read_text()
    assert "open(" not in source


async def test_kind_filter_agrees_across_backends(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spotlight's UTI trees narrow the index; _KIND_EXTS decides what a kind IS.

    "public.text" is an ancestor of "public.source-code", so mdfind answers a
    document query with .py and .json. Without the suffix gate the two backends
    would return different families for the same argument.
    """
    for name in ("doc-note.txt", "doc-script.py", "doc-data.json"):
        (sandbox / name).write_text("x")

    kept = find_files._post_filter(
        [sandbox / "doc-note.txt", sandbox / "doc-script.py", sandbox / "doc-data.json"],
        [sandbox],
        "document",
        20,
        "narrow",
    )
    assert [p.name for _, p in kept] == ["doc-note.txt"]


def test_walk_ranks_newest_globally_not_by_traversal_order(tmp_path: Path) -> None:
    """The newest matches must survive even when the walk reaches them last.

    os.walk yields "aaa" before "zzz", so a backend that truncated at its cap
    would keep the oldest files and drop the newest — while still reporting
    "newest first", because the sort happens after the truncation.
    """
    root = tmp_path / "root"
    (root / "aaa").mkdir(parents=True)
    (root / "zzz").mkdir()
    for i in range(6):
        old_file = root / "aaa" / f"doc-old-{i}.txt"
        old_file.write_text("x")
        os.utime(old_file, (1_000_000 + i, 1_000_000 + i))
    expected = []
    for i in range(4):
        new_file = root / "zzz" / f"doc-new-{i}.txt"
        new_file.write_text("x")
        os.utime(new_file, (2_000_000 + i, 2_000_000 + i))
        expected.append(new_file)

    # limit 1 keeps limit * _WALK_RANK_SLACK == 4 candidates.
    found = find_files._walk([root], "doc", "any", None, 1)

    assert sorted(found) == sorted(expected)


async def test_wildcards_mean_the_same_thing_on_both_backends(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mdfind globs * and ?; the walk compares them literally. Strip them once."""
    captured: list[list[str]] = []
    monkeypatch.setattr(find_files, "current_platform", lambda: "darwin")
    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", _fake_exec(b"", captured))
    await FindFilesAction({}).execute(query="doc*")
    assert '"*doc*"' in captured[0][-1]
    assert "doc**" not in captured[0][-1]

    monkeypatch.setattr(find_files, "current_platform", lambda: "linux")
    result = await FindFilesAction({}).execute(query="doc*")
    assert result.success is True
    assert "doc-a.pdf" in result.output


async def test_explicit_null_limit_uses_the_default(sandbox: Path) -> None:
    """A local model emitting {"limit": null} must not be refused."""
    result = await FindFilesAction({}).execute(query="doc", limit=None)
    assert result.success is True


async def test_mdfind_failure_falls_back_to_the_walk(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-zero exit must not read as an authoritative empty result."""
    monkeypatch.setattr(find_files, "current_platform", lambda: "darwin")

    class _FailingProc(_FakeProc):
        def __init__(self) -> None:
            super().__init__(b"Failed to create query.\n")
            self.returncode = 1

    async def run(*_argv: str, **_kwargs: Any) -> _FailingProc:
        return _FailingProc()

    monkeypatch.setattr(proc_mod.asyncio, "create_subprocess_exec", run)

    result = await FindFilesAction({}).execute(query="doc")
    assert result.success is True
    assert "doc-a.pdf" in result.output


def test_kind_gate_reads_the_symlink_target_not_the_link(tmp_path: Path) -> None:
    """A report.pdf link to a .md file must not answer a kind="pdf" search."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "draft.md").write_text("x")
    (root / "report.pdf").symlink_to(root / "draft.md")

    assert find_files._post_filter([root / "report.pdf"], [root], "pdf", 20, "narrow") == []
    kept = find_files._post_filter([root / "report.pdf"], [root], "document", 20, "narrow")
    assert [p.name for _, p in kept] == ["draft.md"]


def test_a_huge_first_root_does_not_hide_later_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry budget is per root; root order must not decide visibility."""
    monkeypatch.setattr(find_files, "_WALK_MAX_ENTRIES", 20)
    big = tmp_path / "big"
    big.mkdir()
    for i in range(60):
        (big / f"filler-{i}.txt").write_text("x")
    small = tmp_path / "small"
    small.mkdir()
    target = small / "doc-target.txt"
    target.write_text("x")

    assert find_files._walk([big, small], "doc-target", "any", None, 20) == [target]
