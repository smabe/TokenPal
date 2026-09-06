"""Shared path-safety helpers for the filesystem-facing tools."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, NamedTuple

from tokenpal.brain.personality import contains_sensitive_content_term, contains_sensitive_term
from tokenpal.config.loader import load_config

REJECT_PATH = re.compile(r"\.env|credentials|secrets|\.key$|\.pem$", re.IGNORECASE)

_SENSITIVE_PATH_TERMS = frozenset(
    {"keychain", "keeper", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "wallet.dat"}
)
_SENSITIVE_EXTS = frozenset({"key", "pem", "p12", "pfx", "p8", "ovpn", "keystore", "jks"})


async def git_root(start: Path) -> Path | None:
    """Absolute path of the git worktree containing ``start``, or None."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(start),
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    out = stdout.decode("utf-8", errors="replace").strip()
    return Path(out) if out else None


async def allowed_roots(configured: Sequence[str]) -> list[Path]:
    """Resolve configured roots that exist, plus the cwd's git root.

    An explicitly empty ``configured`` is opt-out: it returns no roots at all,
    so emptying ``[paths] allowed_dirs`` turns the file tools off rather than
    silently leaving them the repo the buddy happened to launch from.
    """
    entries = [configured] if isinstance(configured, str) else list(configured)
    if not entries:
        return []
    roots: list[Path] = []
    for entry in entries:
        if not entry.strip():
            continue
        try:
            resolved = Path(entry).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)

    repo = await git_root(Path.cwd())
    if repo is not None:
        resolved_repo = repo.resolve()
        if resolved_repo not in roots:
            roots.append(resolved_repo)
    return roots


def resolve_inside(
    candidate: str | Path, roots: Sequence[Path]
) -> tuple[Path, Path, str] | None:
    """Resolve ``candidate`` and return (resolved, root, rel) for the root containing it.

    Symlinks and ``..`` are followed before the containment check. Case folding
    applies on Windows only: a POSIX root whose case differs from the on-disk
    folder matches nothing. Existence is not checked — that is the caller's job.
    """
    try:
        resolved = Path(candidate).expanduser().resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return None
    folded = Path(os.path.normcase(str(resolved)))
    for root in roots:
        if folded.is_relative_to(Path(os.path.normcase(str(root)))):
            return resolved, root, str(resolved.relative_to(root))
    return None


def _lower(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.lower() for part in parts)


def is_hidden_or_protected(path: Path, root: Path) -> bool:
    """True if ``path`` sits under a dot-directory or an OS-managed directory."""
    home_library = (Path.home() / "Library").parts
    if _lower(path.parts[: len(home_library)]) == _lower(home_library):
        return True
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    for part in rel_parts:
        if part.startswith(".") or part.lower() in {"library", "node_modules"}:
            return True
    return False


def path_is_sensitive(rel: str) -> bool:
    """True if a root-relative path names credentials or an identity-critical app."""
    lower = rel.lower()
    if REJECT_PATH.search(rel) or contains_sensitive_content_term(rel):
        return True
    if any(term in lower for term in _SENSITIVE_PATH_TERMS):
        return True
    # Every dot-separated token, so "server.key.bak" is caught and "notes.keynote" is not.
    return any(
        ext in _SENSITIVE_EXTS
        for part in re.split(r"[\\/]", lower)
        for ext in part.split(".")[1:]
    )


class ResolvedPath(NamedTuple):
    """A path argument the invoker has screened, resolved and contained.

    ``raw`` is what the caller spelled: a tool comparing spellings (read_file's
    dual git-tracking check) has no other source for it once the invoker
    substitutes. ``rel`` is posix so it can be handed to a git pathspec.
    """

    raw: str
    resolved: Path
    root: Path
    rel: str


RootsPolicy = Literal["git_root", "allowed_dirs"]
PathScreen = Literal["broad", "narrow"]

_NO_ROOTS: dict[str, str] = {
    "git_root": "Not inside a git repository.",
    "allowed_dirs": "[paths] allowed_dirs names no folder that exists.",
}
_OUTSIDE: dict[str, str] = {
    "git_root": "Path is outside the git repo.",
    "allowed_dirs": "That path is outside [paths] allowed_dirs.",
}
# One string per screen strength, so a refusal cannot tell the caller whether
# the raw name or the resolved target was the denied one.
_SCREEN_REFUSAL: dict[str, str] = {
    "broad": "Path matches a denied pattern.",
    "narrow": "That path is protected.",
}


async def declared_roots(roots_policy: RootsPolicy) -> list[Path]:
    """Roots admitted by a tool's declared ``path_roots``.

    ``git_root`` yields at most one root and refuses outside a repo, rather
    than falling back to the cwd: "the current repo" has no meaning there.
    """
    if roots_policy == "git_root":
        root = await git_root(Path.cwd())
        return [] if root is None else [root.resolve()]
    return await allowed_roots(load_config().paths.allowed_dirs)


async def resolve_declared_path(
    raw: str, roots_policy: RootsPolicy, screen: PathScreen
) -> tuple[ResolvedPath | None, str]:
    """Screen and contain *raw*, returning ``(path, "")`` or ``(None, refusal)``.

    The order is fixed: raw-name screen, resolve, resolved-name re-screen. Both
    screens are load-bearing — the raw one alone lets a symlink launder a
    denied name, the resolved one alone drops the broad app terms. Refusals are
    fixed strings and never echo the argument: the name can be the secret.
    """
    if screen == "broad":
        if REJECT_PATH.search(raw):
            return None, _SCREEN_REFUSAL["broad"]
        if contains_sensitive_term(raw):
            return None, "Path references a sensitive app."

    roots = await declared_roots(roots_policy)
    if not roots:
        return None, _NO_ROOTS[roots_policy]

    # git_root returns exactly one root, so a relative path anchors at the repo
    # — what read_file and grep_codebase advertise. allowed_dirs has N roots and
    # so no single anchor; a relative path stays anchored at the process cwd.
    candidate: str | Path = roots[0] / raw if roots_policy == "git_root" else raw
    match = resolve_inside(candidate, roots)
    if match is None:
        return None, _OUTSIDE[roots_policy]

    resolved, root, rel = match
    rel = Path(rel).as_posix()
    if path_is_sensitive(rel):
        return None, _SCREEN_REFUSAL[screen]
    return ResolvedPath(raw, resolved, root, rel), ""
