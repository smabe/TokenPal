"""Find files by name under the folders in ``[paths] allowed_dirs``.

Spotlight (``mdfind``) on macOS, a bounded ``os.walk`` everywhere else. Results
are paths and modified times only — the tool never opens a file.
"""

from __future__ import annotations

import asyncio
import heapq
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action
from tokenpal.util.paths import (
    PathScreen,
    declared_roots,
    is_hidden_or_protected,
    is_screened_out,
    resolve_inside,
)
from tokenpal.util.platform import current_platform
from tokenpal.util.proc import run_capture

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_MIN_QUERY_CHARS = 2
_SPOTLIGHT_TIMEOUT_S = 10.0
_WALK_MAX_DEPTH = 8
_WALK_MAX_ENTRIES = 50_000
_WALK_MAX_SECONDS = 3.0
# mdfind cannot sort and has no result limit: a two-character query can emit
# 100k+ paths. This bounds what we materialise, so on a query broader than
# this the result is the newest of an arbitrary slice, not the newest overall.
# The walk has no such cap -- it ranks globally (see _walk).
_SPOTLIGHT_CANDIDATE_CAP = 500
# The walk ranks before _post_filter runs, so it keeps this many times `limit`
# to leave room for candidates the filter drops.
_WALK_RANK_SLACK = 4

_KIND_EXTS: dict[str, frozenset[str]] = {
    # ".key" (Keynote) is omitted: paths.REJECT_PATH denies it either way.
    "document": frozenset(
        {
            ".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pages",
            ".ppt", ".pptx", ".xls", ".xlsx", ".numbers", ".csv",
        }
    ),
    "image": frozenset({".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tiff", ".svg"}),
    "code": frozenset(
        {
            ".py", ".js", ".ts", ".swift", ".rs", ".go", ".c", ".h", ".cpp",
            ".java", ".rb", ".sh", ".toml", ".yaml", ".yml", ".json",
        }
    ),
    "pdf": frozenset({".pdf"}),
}

_KINDS = ("any", *_KIND_EXTS)

# Spotlight narrowing only. These UTI trees do NOT define a kind: "public.text"
# is an ancestor of "public.source-code", so a document query matches .py and
# .json here while _KIND_EXTS files them under code. _post_filter applies
# _KIND_EXTS to both backends so the two agree.
_KIND_TREES: dict[str, tuple[str, ...]] = {
    "document": ("public.composite-content", "public.text"),
    "image": ("public.image",),
    "code": ("public.source-code",),
    "pdf": ("com.adobe.pdf",),
}

_DURATION = re.compile(r"^(\d+)([hdw])$")
_DURATION_UNITS = {"h": 3600, "d": 86_400, "w": 604_800}


def _parse_within(raw: str) -> int | None:
    """Seconds for a ``12h`` / ``2d`` / ``1w`` duration, or None if malformed."""
    match = _DURATION.match(raw.strip())
    if match is None:
        return None
    count = int(match.group(1))
    if count <= 0:
        return None
    return count * _DURATION_UNITS[match.group(2)]


def _escape(query: str) -> str:
    """Escape for a double-quoted Spotlight predicate literal."""
    return query.replace("\\", "\\\\").replace('"', '\\"')


def _spotlight_predicate(query: str, kind: str, since_s: int | None) -> str:
    escaped = _escape(query)
    parts = [
        f'(kMDItemFSName == "*{escaped}*"cd || kMDItemTextContent == "{escaped}*"cd)'
    ]
    trees = _KIND_TREES.get(kind)
    if trees:
        joined = " || ".join(f'kMDItemContentTypeTree == "{tree}"' for tree in trees)
        parts.append(f"({joined})")
    if since_s is not None:
        parts.append(f"(kMDItemContentModificationDate >= $time.now(-{since_s}))")
    return " && ".join(parts)


async def _spotlight(
    roots: list[Path], query: str, kind: str, since_s: int | None
) -> list[Path]:
    """Query the Spotlight index. Raises OSError when mdfind is absent or fails."""
    argv = ["mdfind", "-0"]
    for root in roots:
        argv += ["-onlyin", str(root)]
    argv.append(_spotlight_predicate(query, kind, since_s))

    returncode, stdout, _ = await run_capture(argv, timeout_s=_SPOTLIGHT_TIMEOUT_S)

    if returncode != 0:
        # mdfind writes its diagnostic to stdout and can also exit 0 on an
        # unindexed volume; a non-zero code is the only signal we get, and it
        # must not read as an authoritative "no matches".
        raise OSError(f"mdfind exited {returncode}")

    head = stdout.split(b"\0", _SPOTLIGHT_CANDIDATE_CAP)[:_SPOTLIGHT_CANDIDATE_CAP]
    return [Path(raw.decode("utf-8", errors="replace")) for raw in head if raw]


def _walk(
    roots: list[Path], query: str, kind: str, since_ts: float | None, limit: int
) -> list[Path]:
    """The newest matches, ranked across everything the traversal budget reaches."""
    keep = limit * _WALK_RANK_SLACK
    needle = query.lower()
    exts = _KIND_EXTS.get(kind)
    deadline = time.monotonic() + _WALK_MAX_SECONDS
    # (mtime, tiebreak, path): a bounded min-heap, so the walk keeps the newest
    # matches globally rather than the first ones it happens to reach.
    newest: list[tuple[float, int, Path]] = []
    seq = 0

    for root in roots:
        # Per root: a first root over the entry cap must not hide the rest.
        entries = 0
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            here = Path(dirpath)
            if len(here.parts) - root_depth >= _WALK_MAX_DEPTH:
                dirnames[:] = []
            else:
                # Also the traversal budget: without pruning, the entry cap and
                # the deadline drain into node_modules/.git and the walk starves
                # before reaching real matches. _post_filter re-checks anyway.
                dirnames[:] = [
                    d for d in dirnames if not is_hidden_or_protected(here / d, root)
                ]
            entries += len(dirnames) + len(filenames)

            for name in filenames:
                if needle not in name.lower():
                    continue
                if exts is not None and Path(name).suffix.lower() not in exts:
                    continue
                candidate = here / name
                try:
                    mtime = os.stat(candidate).st_mtime
                except OSError:
                    continue
                if since_ts is not None and mtime < since_ts:
                    continue
                seq += 1
                if len(newest) < keep:
                    heapq.heappush(newest, (mtime, seq, candidate))
                else:
                    heapq.heappushpop(newest, (mtime, seq, candidate))

            if time.monotonic() > deadline:
                return [path for _, _, path in newest]
            if entries >= _WALK_MAX_ENTRIES:
                break

    return [path for _, _, path in newest]


async def _run_backend(
    plat: str,
    roots: list[Path],
    query: str,
    kind: str,
    since_s: int | None,
    limit: int,
) -> list[Path]:
    """Unfiltered candidate paths from the backend that fits *plat*."""
    if plat == "darwin":
        try:
            return await _spotlight(roots, query, kind, since_s)
        except TimeoutError:
            # A subclass of OSError, and the one failure the user must be told
            # about rather than silently answered from a slower backend.
            raise
        except (OSError, NotImplementedError):
            pass
    since_ts = None if since_s is None else time.time() - since_s
    return await asyncio.to_thread(_walk, roots, query, kind, since_ts, limit)


def _post_filter(
    candidates: list[Path], roots: list[Path], kind: str, limit: int, screen: PathScreen
) -> list[tuple[float, Path]]:
    """Drop anything outside the roots, hidden, protected, sensitive, or off-kind."""
    kept: list[tuple[float, Path]] = []
    seen: set[Path] = set()
    exts = _KIND_EXTS.get(kind)

    for candidate in candidates:
        match = resolve_inside(candidate, roots)
        if match is None:
            continue
        resolved, root, rel = match
        # The resolved target, not the candidate: a "report.pdf" symlink to a
        # .md file must not answer a kind="pdf" search with the .md path.
        if exts is not None and resolved.suffix.lower() not in exts:
            continue
        if resolved in seen:
            continue
        # "narrow": the tool screens names it found, and the broad app terms
        # would refuse a benign file under a badly-named folder.
        if is_screened_out(resolved, root, rel, screen):
            continue
        try:
            info = os.stat(resolved)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        seen.add(resolved)
        kept.append((info.st_mtime, resolved))

    kept.sort(key=lambda hit: hit[0], reverse=True)
    return kept[:limit]


def _refuse(argument: str, detail: str) -> ActionResult:
    return ActionResult(output=f"{argument} {detail}", success=False)


@register_action
class FindFilesAction(AbstractAction):
    action_name = "find_files"
    description = (
        "Find files by name (and on macOS by indexed content) under the folders in "
        "[paths] allowed_dirs. Returns paths and modified times, newest first, never "
        "file contents. Use modified_within like '2d' for 'what was I working on'."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    f"Text to match in the file name ({_MIN_QUERY_CHARS}+ characters)."
                ),
            },
            "kind": {
                "type": "string",
                "enum": list(_KINDS),
                "description": "Restrict to a file family. Defaults to any.",
            },
            "modified_within": {
                "type": "string",
                "description": "Only files touched this recently, e.g. '12h', '2d', '1w'.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
                "description": f"Maximum results, 1-{_MAX_LIMIT}. Defaults to {_DEFAULT_LIMIT}.",
            },
        },
        "required": ["query"],
    }
    safe = True
    requires_confirm = False
    # No path ARGUMENT, so the invoker resolves nothing -- this tool filters
    # paths a backend returned. Declared and READ below, so the class cannot
    # drift from what it actually does.
    path_roots = "allowed_dirs"
    path_screen = "narrow"
    # The listing goes stale the moment the user saves a file mid-run.
    cacheable: ClassVar[bool] = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        query = kwargs.get("query", "")
        # A query of bare wildcards would match every file under every root.
        literal = query.replace("*", "").replace("?", "").strip() if isinstance(query, str) else ""
        if len(literal) < _MIN_QUERY_CHARS:
            return _refuse("query", f"needs at least {_MIN_QUERY_CHARS} non-wildcard characters.")
        # Both backends search the stripped literal as a substring. mdfind would
        # treat * and ? as globs while the walk compares them literally, so the
        # same query would mean different things per platform.
        query = literal

        kind = kwargs.get("kind") or "any"
        if kind not in _KINDS:
            return _refuse("kind", f"must be one of {', '.join(_KINDS)}.")

        since_s: int | None = None
        raw_within = kwargs.get("modified_within")
        if raw_within is not None:
            since_s = _parse_within(raw_within) if isinstance(raw_within, str) else None
            if since_s is None:
                return _refuse("modified_within", "must look like '12h', '2d' or '1w'.")

        raw_limit = kwargs.get("limit")
        try:
            limit = _DEFAULT_LIMIT if raw_limit is None else int(raw_limit)
        except (TypeError, ValueError):
            limit = 0
        if limit < 1:
            return _refuse("limit", f"must be a whole number from 1 to {_MAX_LIMIT}.")
        limit = min(limit, _MAX_LIMIT)

        roots = await declared_roots(self.path_roots)
        if not roots:
            return _refuse("[paths] allowed_dirs", "names no folder that exists.")

        try:
            candidates = await _run_backend(
                current_platform(), roots, query, kind, since_s, limit
            )
        except TimeoutError:
            return ActionResult(
                output=f"File search timed out after {_SPOTLIGHT_TIMEOUT_S:.0f}s.",
                success=False,
            )

        hits = _post_filter(candidates, roots, kind, limit, self.path_screen)
        if not hits:
            return ActionResult(output=f"No matches under {len(roots)} allowed folders.")
        return ActionResult(
            output="\n".join(
                f"{datetime.fromtimestamp(mtime):%Y-%m-%d %H:%M}  {path}" for mtime, path in hits
            )
        )
