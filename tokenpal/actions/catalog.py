"""Human-readable tool catalog surfaced by the /tools picker.

Defaults stay on without an opt-in; everything else is grouped by section so
the Textual picker can render stacked SelectionList widgets. Consent
categories flag tools that need network-fetch or external-key consent before
they will run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    blurb: str
    consent_category: str = ""


@dataclass(frozen=True)
class CatalogSection:
    key: str
    title: str
    entries: tuple[CatalogEntry, ...]


DEFAULT_SECTION = CatalogSection(
    key="default",
    title="Default",
    entries=(
        CatalogEntry("timer", "Set a countdown timer."),
        CatalogEntry("system_info", "Report CPU, RAM, disk, battery."),
        CatalogEntry("open_app", "Open an allowlisted app."),
        CatalogEntry("do_math", "Evaluate a pure arithmetic expression."),
    ),
)


LOCAL_SECTION = CatalogSection(
    key="local",
    title="Local",
    entries=(
        CatalogEntry("read_file", "Read a git-tracked file, capped at 200KB."),
        CatalogEntry("grep_codebase", "Search the current repo with ripgrep."),
        CatalogEntry("git_log", "Show recent commits."),
        CatalogEntry("git_diff", "Show the current diff, capped at 50KB."),
        CatalogEntry("git_status", "Show working-tree status."),
        CatalogEntry("list_processes", "List top processes by CPU then RSS."),
        CatalogEntry("memory_query", "Query local session history metrics."),
    ),
)


SECTIONS: tuple[CatalogSection, ...] = (DEFAULT_SECTION, LOCAL_SECTION)
