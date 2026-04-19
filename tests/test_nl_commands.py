"""Tests for the natural-language command matcher."""

from __future__ import annotations

import pytest

from tokenpal.nl_commands import match_nl_command


@pytest.mark.parametrize(
    "text,expected",
    [
        ("give me a summary", ("summary", "")),
        ("Give me the summary", ("summary", "")),
        ("gimme a recap", ("summary", "")),
        ("show me a summary", ("summary", "")),
        ("daily summary", ("summary", "")),
        ("end of day summary", ("summary", "")),
        ("end-of-day recap", ("summary", "")),
        ("summarize", ("summary", "")),
        ("recap", ("summary", "")),
        ("what did I do", ("summary", "")),
        ("summarize today", ("summary", "today")),
        ("summarise today", ("summary", "today")),
        ("recap yesterday", ("summary", "yesterday")),
        ("today's summary", ("summary", "today")),
        ("yesterdays recap", ("summary", "yesterday")),
        ("what did I do today", ("summary", "today")),
        ("what did I do yesterday", ("summary", "yesterday")),
        ("what did I accomplish yesterday", ("summary", "yesterday")),
        # Trailing punctuation tolerated.
        ("give me a summary?", ("summary", "")),
        ("summarize today!", ("summary", "today")),
    ],
)
def test_summary_patterns(text: str, expected: tuple[str, str]) -> None:
    assert match_nl_command(text) == expected


@pytest.mark.parametrize(
    "text,expected_args",
    [
        ("remind me to finish the auth PR", "finish the auth PR"),
        ("Remind me finish the auth PR", "finish the auth PR"),
        ("my goal is ship the release", "ship the release"),
        ("my intent is ship the release", "ship the release"),
        ("my focus is ship the release", "ship the release"),
        ("my goal to ship the release", "ship the release"),
        ("set my goal to ship the release", "ship the release"),
        ("set my intent to ship the release", "ship the release"),
        ("set goal ship the release", "ship the release"),
        ("I'm working on the migration", "the migration"),
        ("im working on the migration", "the migration"),
        ("I am working on the migration", "the migration"),
        ("I'm currently working on the migration", "the migration"),
        # Trailing punctuation stripped from the goal too.
        ("remind me to finish the auth PR.", "finish the auth PR"),
    ],
)
def test_intent_set_patterns(text: str, expected_args: str) -> None:
    assert match_nl_command(text) == ("intent", expected_args)


@pytest.mark.parametrize(
    "text",
    [
        "what am I working on",
        "what's my goal",
        "whats my intent",
        "what is my focus",
        "show my goal",
        "show my intent",
        "check my goal",
        "what am I working on?",
    ],
)
def test_intent_status_patterns(text: str) -> None:
    assert match_nl_command(text) == ("intent", "status")


@pytest.mark.parametrize(
    "text",
    [
        "clear my goal",
        "forget my intent",
        "cancel my focus",
        "reset my goal",
        "drop my intent",
        "delete my goal",
        "clear goal",
    ],
)
def test_intent_clear_patterns(text: str) -> None:
    assert match_nl_command(text) == ("intent", "clear")


@pytest.mark.parametrize(
    "text",
    [
        # Timer-like reminders fall through to the LLM / timer tool.
        "remind me in 5 minutes to drink water",
        "remind me in 30 min to stand up",
        "remind me in 2 hours to take a break",
        "remind me in 10s to check the oven",
        # Empty-goal "remind me to" with nothing after shouldn't set an intent.
        "remind me to",
        # Slash-prefixed input should not be re-routed.
        "/summary today",
        "/intent clear",
        # Empty / whitespace-only.
        "",
        "   ",
        # Arbitrary conversation mustn't trigger.
        "how are you",
        "what's up",
        "tell me a joke",
        "did you see that?",
        # "recap" embedded in a sentence, not bare.
        "give me a quick recap of what we just discussed please",
        # "summary" as a word in conversation.
        "that's a good summary of the problem",
        # Mid-sentence "remind me to X" — anchored regex keeps it out.
        "hey, remind me to finish the PR",
        # Conversational variant we deliberately don't handle.
        "what am I doing",
        # Slash with trailing whitespace.
        "/summary   ",
    ],
)
def test_non_matches(text: str) -> None:
    assert match_nl_command(text) is None
