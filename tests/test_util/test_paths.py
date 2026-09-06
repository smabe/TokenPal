"""Tests for the shared path-safety helpers."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenpal.util import paths
from tokenpal.util.paths import (
    allowed_roots,
    is_hidden_or_protected,
    path_is_sensitive,
    resolve_declared_path,
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


# --- resolve_declared_path: the policy the invoker enforces ---


def _stub_git_root(monkeypatch: pytest.MonkeyPatch, repo: Path | None) -> list[int]:
    """Point ``git_root`` at *repo*; the returned list counts the calls."""
    calls: list[int] = []

    async def fake(_start: Path) -> Path | None:
        calls.append(1)
        return repo

    monkeypatch.setattr(paths, "git_root", fake)
    return calls


def _stub_allowed_dirs(monkeypatch: pytest.MonkeyPatch, *dirs: Path) -> None:
    cfg = SimpleNamespace(paths=SimpleNamespace(allowed_dirs=[str(d) for d in dirs]))
    monkeypatch.setattr(paths, "load_config", lambda: cfg)
    _stub_git_root(monkeypatch, None)


async def test_resolve_declared_path_carries_raw_resolved_root_and_rel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tuple, not a bare resolved string: read_file compares the spelled
    name against ``rel`` and needs ``root`` for ``git -C``."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x")
    _stub_git_root(monkeypatch, tmp_path)

    path, refusal = await resolve_declared_path("docs/a.md", "git_root", "broad")

    assert refusal == ""
    assert path == paths.ResolvedPath(
        raw="docs/a.md",
        resolved=(tmp_path / "docs" / "a.md").resolve(),
        root=tmp_path.resolve(),
        rel="docs/a.md",
    )


async def test_a_denied_raw_name_refuses_before_any_roots_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REJECT_PATH on the raw spelling, the broad screen's first gate."""
    _stub_git_root(monkeypatch, tmp_path)

    path, refusal = await resolve_declared_path(".env", "git_root", "broad")

    assert path is None
    assert refusal == "Path matches a denied pattern."


async def test_broad_screen_refuses_the_raw_name_before_any_roots_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_git_root(monkeypatch, tmp_path)

    _, refusal = await resolve_declared_path("notes/1password.txt", "git_root", "broad")

    assert refusal == "Path references a sensitive app."
    assert calls == [], "the raw screen must refuse before the git subprocess"


async def test_narrow_screen_does_not_screen_the_raw_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw ABSOLUTE path under a badly-named allowed root would be refused by
    the broad screen, and open_path accepts it today."""
    root = tmp_path / "credentials-app"
    root.mkdir()
    target = root / "README.md"
    target.write_text("x")
    _stub_allowed_dirs(monkeypatch, root)

    narrow, _ = await resolve_declared_path(str(target), "allowed_dirs", "narrow")
    broad, refusal = await resolve_declared_path(str(target), "allowed_dirs", "broad")

    assert narrow is not None and narrow.rel == "README.md"
    assert broad is None and refusal == "Path matches a denied pattern."


@pytest.mark.parametrize("screen", ["broad", "narrow"])
async def test_the_resolved_name_is_screened_whatever_the_raw_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, screen: str
) -> None:
    """A symlink spelled benignly must be refused as what it opens."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "id_rsa").write_text("key")
    (root / "notes.txt").symlink_to(root / "id_rsa")
    _stub_allowed_dirs(monkeypatch, root)

    path, refusal = await resolve_declared_path(
        str(root / "notes.txt"), "allowed_dirs", screen
    )

    assert path is None
    # The exact string, so an earlier refusal (no roots, outside roots) cannot
    # stand in for the resolved-name screen this test exists to pin.
    assert refusal == (
        "Path matches a denied pattern." if screen == "broad" else "That path is protected."
    )


async def test_git_root_policy_refuses_outside_a_repo(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_git_root(monkeypatch, None)

    path, refusal = await resolve_declared_path("a.txt", "git_root", "broad")

    assert path is None
    assert refusal == "Not inside a git repository."


async def test_allowed_dirs_policy_refuses_when_the_list_is_empty(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_allowed_dirs(monkeypatch)

    path, refusal = await resolve_declared_path("a.txt", "allowed_dirs", "narrow")

    assert path is None
    assert "[paths] allowed_dirs" in refusal


async def test_a_relative_path_anchors_at_the_repo_under_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_inside`` anchors relatives at the process cwd, so the policy
    has to join the root explicitly or the meaning silently changes."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _stub_git_root(monkeypatch, repo)

    path, _ = await resolve_declared_path("docs/a.md", "git_root", "broad")

    assert path is not None
    assert path.resolved == (repo / "docs" / "a.md").resolve()


async def test_a_relative_path_stays_cwd_anchored_under_allowed_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N roots means no single anchor, which is why open_path works this way.

    The cwd is a SUBDIRECTORY of the root, so cwd-anchoring and root-anchoring
    give different answers and the assertion can tell them apart.
    """
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.pdf").write_text("root copy")
    (root / "sub" / "a.pdf").write_text("sub copy")
    monkeypatch.chdir(root / "sub")
    _stub_allowed_dirs(monkeypatch, root)

    path, _ = await resolve_declared_path("a.pdf", "allowed_dirs", "narrow")

    assert path is not None
    assert path.resolved == (root / "sub" / "a.pdf").resolve()
    assert path.rel == "sub/a.pdf"


@pytest.mark.parametrize("policy", ["git_root", "allowed_dirs"])
async def test_refusals_never_echo_the_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str
) -> None:
    """The name itself can be the secret."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "acquisition-memo.pdf").write_text("x")
    if policy == "allowed_dirs":
        _stub_allowed_dirs(monkeypatch, root)
    else:
        _stub_git_root(monkeypatch, root)

    _, refusal = await resolve_declared_path(
        str(outside / "acquisition-memo.pdf"), policy, "narrow"
    )

    assert "acquisition-memo" not in refusal
    assert str(tmp_path) not in refusal
