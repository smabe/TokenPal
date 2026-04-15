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
from typing import Literal

from tokenpal.config.schema import DEFAULT_TOOLS

Kind = Literal["default", "local", "utility", "focus", "agent", "research"]


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    blurb: str
    kind: Kind = "default"
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
    entries=(
        CatalogEntry("read_file", "Read a git-tracked file, capped at 200KB.", kind="local"),
        CatalogEntry("grep_codebase", "Search the current repo with ripgrep.", kind="local"),
        CatalogEntry("git_log", "Show recent commits.", kind="local"),
        CatalogEntry("git_diff", "Show the current diff, capped at 50KB.", kind="local"),
        CatalogEntry("git_status", "Show working-tree status.", kind="local"),
        CatalogEntry("list_processes", "List top processes by CPU then RSS.", kind="local"),
        CatalogEntry("memory_query", "Query local session history metrics.", kind="local"),
    ),
)
UTILITIES_SECTION = CatalogSection(
    title="Utilities",
    description="Everyday lookups. Some hit the public internet.",
    entries=(
        CatalogEntry(
            "convert",
            "Convert between units (miles/km, lb/kg, F/C, etc.).",
            kind="utility",
        ),
        CatalogEntry("timezone", "Current local time for a named city.", kind="utility"),
        CatalogEntry("sunrise_sunset", "Today's sunrise, solar noon, and sunset.", kind="utility"),
        CatalogEntry("moon_phase", "Moon phase and illumination for a date.", kind="utility"),
        CatalogEntry(
            "currency",
            "Convert amounts between currencies (open.er-api.com).",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "weather_forecast_week",
            "7-day forecast for the configured /zip location.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "pollen_count",
            "Current pollen counts (alder, birch, grass, ragweed).",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "air_quality",
            "Current AQI, PM2.5 and PM10 for your location.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "random_fact",
            "Fetch a random trivia fact (uselessfacts.jsph.pl).",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "joke_of_the_day",
            "Fetch a random dad joke (icanhazdadjoke.com).",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "word_of_the_day",
            "Today's Wordnik word of the day via RSS.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "on_this_day",
            "Historical events for today (Wikimedia).",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "random_recipe",
            "Random recipe from TheMealDB, optionally by ingredient.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "trivia_question",
            "Multiple-choice trivia question from OpenTDB.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "sports_score",
            "Recent results for a team via TheSportsDB.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "crypto_price",
            "Current USD crypto price via CoinGecko public API.",
            kind="utility",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "book_suggestion",
            "Random book pick by genre via Google Books.",
            kind="utility",
            consent_category="web_fetches",
        ),
    ),
)
FOCUS_SECTION = CatalogSection(
    title="Focus",
    description="Pomodoro, reminders, and habit wrappers.",
    entries=(
        CatalogEntry(
            "pomodoro",
            "Start a work/break cycle with in-character phase announcements.",
            kind="focus",
        ),
        CatalogEntry(
            "stretch_reminder",
            "Recurring stretch nudge (speech bubble). Pauses in conversation.",
            kind="focus",
        ),
        CatalogEntry(
            "water_reminder",
            "Recurring hydration nudge. Pauses in conversation.",
            kind="focus",
        ),
        CatalogEntry(
            "eye_break",
            "Recurring 20-20-20 eye-rest prompt. Pauses in conversation.",
            kind="focus",
        ),
        CatalogEntry(
            "bedtime_wind_down",
            "Recurring wrap-up nudges starting 60 minutes before bedtime.",
            kind="focus",
        ),
        CatalogEntry(
            "hydration_log",
            "Log fluid intake and report today's running total.",
            kind="focus",
        ),
        CatalogEntry(
            "habit_streak",
            "Track a named habit and report current + longest day streak.",
            kind="focus",
        ),
        CatalogEntry(
            "mood_check",
            "Prompt a one-word mood check and record the reply if given.",
            kind="focus",
        ),
    ),
)
AGENT_SECTION = CatalogSection(
    title="Agent",
    description=(
        "Multi-step agent mode. Runs /agent <goal> as a tool-calling loop "
        "with step cap, token budget, and per-tool confirm gate."
    ),
    entries=(
        CatalogEntry(
            "agent_mode",
            "Enable /agent <goal> — chains tools toward a goal with confirm prompts.",
            kind="agent",
        ),
    ),
)
RESEARCH_SECTION = CatalogSection(
    title="Research",
    description=(
        "Plan-search-read-synthesize pipeline. /research gates on the "
        "research_mode flag plus web_fetches consent."
    ),
    entries=(
        CatalogEntry(
            "research_mode",
            "Enable /research <question> — plans queries, searches, cites sources.",
            kind="research",
            consent_category="research_mode",
        ),
        CatalogEntry(
            "search_web",
            "Search DuckDuckGo or Wikipedia for a single query.",
            kind="research",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "fetch_url",
            "Fetch a URL and extract clean article text (no JS).",
            kind="research",
            consent_category="web_fetches",
        ),
    ),
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


def find_entry(name: str) -> tuple[CatalogEntry, CatalogSection] | None:
    """Look up a catalog entry plus its owning section by tool name."""
    for section in SECTIONS:
        for entry in section.entries:
            if entry.name == name:
                return entry, section
    return None
