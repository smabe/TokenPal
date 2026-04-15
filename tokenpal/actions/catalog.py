"""Static catalog of tools grouped by section for the /tools picker.

The registry (``registry.py``) only knows *which tool classes exist*. The
catalog knows *how to present them to humans* — which section they live in,
what blurb to show, and whether they require a consent category before the
network check at call time.

Phase 0 ships with only the four defaults. Phase 1-5 will append new entries
here as new tools land.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tokenpal.config.schema import DEFAULT_TOOLS


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    blurb: str
    # One of the consent categories from config.consent.Category, or "" if
    # the tool needs no network / external gate.
    consent_category: str = ""


@dataclass(frozen=True)
class CatalogSection:
    title: str
    description: str
    entries: tuple[CatalogEntry, ...] = field(default_factory=tuple)


DEFAULT_SECTION = CatalogSection(
    title="Default",
    description="Always on. Core tools shipped with TokenPal.",
    entries=(
        CatalogEntry("timer", "Set a countdown timer with a spoken alert."),
        CatalogEntry("system_info", "Report CPU/memory/disk usage."),
        CatalogEntry("open_app", "Launch an application by name."),
        CatalogEntry("do_math", "Evaluate an arithmetic expression."),
    ),
)

# Phase 1-5 populate these as tools land. Kept in Plan order so the modal
# reads top-down as the roadmap does.
LOCAL_SECTION = CatalogSection(
    title="Local",
    description="Power-user tools that read local state (no network).",
)
UTILITIES_SECTION = CatalogSection(
    title="Utilities",
    description="Everyday lookups. Some hit the public internet.",
)
FOCUS_SECTION = CatalogSection(
    title="Focus",
    description="Pomodoro, reminders, and habit wrappers.",
)
AGENT_SECTION = CatalogSection(
    title="Agent",
    description="Multi-step agent mode. Chains tools toward a goal.",
)
RESEARCH_SECTION = CatalogSection(
    title="Research",
    description="Plan-search-read-synthesize research pipeline.",
)


SECTIONS: tuple[CatalogSection, ...] = (
    DEFAULT_SECTION,
    LOCAL_SECTION,
    UTILITIES_SECTION,
    FOCUS_SECTION,
    AGENT_SECTION,
    RESEARCH_SECTION,
)


def default_tool_names() -> frozenset[str]:
    return frozenset(DEFAULT_TOOLS)


def all_optin_entries() -> tuple[CatalogEntry, ...]:
    """Every non-default catalog entry, flattened."""
    return tuple(
        entry
        for section in SECTIONS
        if section is not DEFAULT_SECTION
        for entry in section.entries
    )
