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
UTILITIES_SECTION = CatalogSection(
    title="Utilities",
    description="Everyday lookups. Some hit the public internet.",
    entries=(
        CatalogEntry("convert", "Convert between units (miles/km, lb/kg, F/C, etc.)."),
        CatalogEntry("timezone", "Current local time for a named city."),
        CatalogEntry("sunrise_sunset", "Today's sunrise, solar noon, and sunset."),
        CatalogEntry("moon_phase", "Moon phase and illumination for a date."),
        CatalogEntry(
            "currency",
            "Convert amounts between currencies (open.er-api.com).",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "weather_forecast_week",
            "7-day forecast for the configured /zip location.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "pollen_count",
            "Current pollen counts (alder, birch, grass, ragweed).",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "air_quality",
            "Current AQI, PM2.5 and PM10 for your location.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "random_fact",
            "Fetch a random trivia fact (uselessfacts.jsph.pl).",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "joke_of_the_day",
            "Fetch a random dad joke (icanhazdadjoke.com).",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "word_of_the_day",
            "Today's Wordnik word of the day via RSS.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "on_this_day",
            "Historical events for today (Wikimedia).",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "random_recipe",
            "Random recipe from TheMealDB, optionally by ingredient.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "trivia_question",
            "Multiple-choice trivia question from OpenTDB.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "sports_score",
            "Recent results for a team via TheSportsDB.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "crypto_price",
            "Current USD crypto price via CoinGecko public API.",
            consent_category="web_fetches",
        ),
        CatalogEntry(
            "book_suggestion",
            "Random book pick by genre via Google Books.",
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
        ),
        CatalogEntry(
            "stretch_reminder",
            "Recurring stretch nudge (speech bubble). Pauses in conversation.",
        ),
        CatalogEntry(
            "water_reminder",
            "Recurring hydration nudge. Pauses in conversation.",
        ),
        CatalogEntry(
            "eye_break",
            "Recurring 20-20-20 eye-rest prompt. Pauses in conversation.",
        ),
        CatalogEntry(
            "bedtime_wind_down",
            "Recurring wrap-up nudges starting 60 minutes before bedtime.",
        ),
        CatalogEntry(
            "hydration_log",
            "Log fluid intake and report today's running total.",
        ),
        CatalogEntry(
            "habit_streak",
            "Track a named habit and report current + longest day streak.",
        ),
        CatalogEntry(
            "mood_check",
            "Prompt a one-word mood check and record the reply if given.",
        ),
    ),
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
