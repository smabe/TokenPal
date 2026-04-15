"""Stop-reason enums for the agent and research runners."""

from __future__ import annotations

from enum import StrEnum


class AgentStopReason(StrEnum):
    COMPLETE = "complete"
    STEP_CAP = "step_cap"
    TOKEN_BUDGET = "token_budget"
    SENSITIVE = "sensitive"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    UNAVAILABLE = "unavailable"


class ResearchStopReason(StrEnum):
    COMPLETE = "complete"
    NO_QUERIES = "no_queries"
    NO_SOURCES = "no_sources"
    TOKEN_BUDGET = "token_budget"
    TIMEOUT = "timeout"
    CRASHED = "crashed"
    UNAVAILABLE = "unavailable"
