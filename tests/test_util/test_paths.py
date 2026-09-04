"""Tests for the shared path-safety helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tokenpal.util import paths
from tokenpal.util.paths import (
    allowed_roots,
    is_hidden_or_protected,
    path_is_sensitive,
    resolve_inside,
)


def test_symlink_escaping_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "id_rsa"
    secret.write_text("key")
    link = root / "link.txt"
    link.symlink_to(secret)

    assert str(link).startswith(str(root))
    assert resolve_inside(link, [root]) is None


def test_dotdot_escaping_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()

    assert resolve_inside(root / ".." / "outside", [root]) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX case policy")
def test_case_mismatched_root_refuses_on_posix(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    assert resolve_inside(root / "a.pdf", [Path(str(root).upper())]) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows case folding")
def test_case_mismatched_root_accepts_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "a.pdf"

    match = resolve_inside(target, [Path(str(root).upper())])
    assert match is not None
    assert match[0] == target.resolve()
    assert match[2] == "a.pdf"


def test_nonexistent_candidate_inside_root_resolves(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    match = resolve_inside(root / "not-there.pdf", [root])
    assert match == ((root / "not-there.pdf").resolve(), root, "not-there.pdf")


def test_empty_roots_never_match(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, []) is None


async def test_allowed_roots_filters_expands_and_appends_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    docs = home / "Documents"
    docs.mkdir(parents=True)
    a_file = home / "note.txt"
    a_file.write_text("x")
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    async def fake_git_root(start: Path) -> Path | None:
        return repo

    monkeypatch.setattr(paths, "git_root", fake_git_root)

    roots = await allowed_roots(
        ["~/Documents", "~/Missing", str(a_file), "", str(repo)]
    )
    assert roots == [docs.resolve(), repo.resolve()]


async def test_allowed_roots_without_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_git_root(start: Path) -> Path | None:
        return None

    monkeypatch.setattr(paths, "git_root", no_git_root)

    assert await allowed_roots([str(tmp_path)]) == [tmp_path.resolve()]


def test_is_hidden_or_protected(tmp_path: Path) -> None:
    root = tmp_path
    assert is_hidden_or_protected(root / ".ssh" / "id_rsa", root) is True
    assert is_hidden_or_protected(root / "Library" / "x", root) is True
    assert is_hidden_or_protected(root / "node_modules" / "x", root) is True
    assert is_hidden_or_protected(root / "Documents" / "a.pdf", root) is False


def test_is_hidden_or_protected_covers_home_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / "Library" / "Mail"

    assert is_hidden_or_protected(root / "note.txt", root) is True


@pytest.mark.parametrize(
    "rel",
    ["x.env", "credentials.json", "keychain-backup.txt", "1password-export.csv"],
)
def test_path_is_sensitive_rejects(rel: str) -> None:
    assert path_is_sensitive(rel) is True


@pytest.mark.parametrize("rel", ["health-tracker.csv", "signal-report.md"])
def test_path_is_sensitive_allows_ordinary_words(rel: str) -> None:
    assert path_is_sensitive(rel) is False


async def test_allowed_roots_treats_a_bare_string_as_one_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scalar in allowed_dirs must not iterate as characters and admit "/"."""

    async def no_git_root(start: Path) -> Path | None:
        return None

    monkeypatch.setattr(paths, "git_root", no_git_root)

    roots = await allowed_roots(str(tmp_path))
    assert roots == [tmp_path.resolve()]


@pytest.mark.parametrize("entry", ["~nosuchuser42/Docs", "a\x00b"])
async def test_allowed_roots_skips_unresolvable_entries(
    entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_git_root(start: Path) -> Path | None:
        return None

    monkeypatch.setattr(paths, "git_root", no_git_root)

    assert await allowed_roots([entry, str(tmp_path)]) == [tmp_path.resolve()]


@pytest.mark.parametrize("candidate", ["~nosuchuser42/x", "a\x00b"])
def test_resolve_inside_refuses_unresolvable_candidates(
    candidate: str, tmp_path: Path
) -> None:
    assert resolve_inside(candidate, [tmp_path]) is None


def test_is_hidden_or_protected_refuses_a_non_descendant(tmp_path: Path) -> None:
    assert is_hidden_or_protected(Path("/etc/passwd"), tmp_path) is True


@pytest.mark.parametrize("name", ["Library", "library", "LIBRARY", "node_Modules"])
def test_is_hidden_or_protected_is_case_insensitive(name: str, tmp_path: Path) -> None:
    assert is_hidden_or_protected(tmp_path / name / "x", tmp_path) is True


def test_is_hidden_or_protected_does_not_prefix_match_home_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert is_hidden_or_protected(tmp_path / "Library2" / "x", tmp_path) is False
    assert is_hidden_or_protected(tmp_path / "library" / "Mail" / "x", tmp_path) is True


def test_resolve_inside_returns_the_root_relative_path(tmp_path: Path) -> None:
    """Callers get rel from the match rather than re-deriving relative_to."""
    nested = tmp_path / "Archive" / "2026"
    nested.mkdir(parents=True)

    match = resolve_inside(nested / "a.pdf", [tmp_path])
    assert match is not None
    assert match[2] == str(Path("Archive") / "2026" / "a.pdf")


@pytest.mark.parametrize(
    "rel",
    [
        "backup/id_rsa",
        "backup/id_ed25519",
        "certs/client.p12",
        "certs/client.pfx",
        "apple/AuthKey.p8",
        "vpn/work.ovpn",
        "server.key.bak",
        "site.pem.old",
        "wallet.dat",
        "keeper-vault-export.csv",
    ],
)
def test_path_is_sensitive_rejects_key_material_and_password_managers(rel: str) -> None:
    assert path_is_sensitive(rel) is True


@pytest.mark.parametrize("rel", ["notes.keynote", "monkey-photos/a.jpg", "hockey.pdf"])
def test_path_is_sensitive_does_not_false_match_ordinary_extensions(rel: str) -> None:
    assert path_is_sensitive(rel) is False


async def test_empty_allowed_dirs_is_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emptying the config list must disable the tools, not fall back to the repo."""

    async def real_looking_git_root(_start: Path) -> Path | None:
        return Path.cwd()

    monkeypatch.setattr(paths, "git_root", real_looking_git_root)

    assert await allowed_roots([]) == []
