"""Verify the sensitive-apps list is unified — productivity imports from
personality rather than maintaining a duplicate definition."""

from __future__ import annotations

from tokenpal.brain import personality
from tokenpal.brain.personality import SENSITIVE_APPS, contains_sensitive_term
from tokenpal.senses.productivity import memory_stats


def test_sensitive_apps_importable_and_non_empty():
    assert isinstance(SENSITIVE_APPS, list)
    assert len(SENSITIVE_APPS) > 0
    assert any("1password" in s.lower() for s in SENSITIVE_APPS)


def test_productivity_memory_stats_uses_unified_list():
    assert isinstance(memory_stats._SENSITIVE_APPS, set)
    assert memory_stats._SENSITIVE_APPS == set(SENSITIVE_APPS)


def test_productivity_imports_canonical_symbol():
    assert memory_stats.SENSITIVE_APPS is personality.SENSITIVE_APPS


def test_contains_sensitive_term_positive_case_insensitive():
    assert contains_sensitive_term("I'm on 1Password") is True
    assert contains_sensitive_term("opening 1PASSWORD now") is True
    assert contains_sensitive_term("check my Chase balance") is True


def test_contains_sensitive_term_negative():
    assert contains_sensitive_term("hello world") is False
    assert contains_sensitive_term("working on some code") is False


def test_contains_sensitive_term_empty_string():
    assert contains_sensitive_term("") is False


def test_contains_sensitive_term_none_handled_gracefully():
    assert contains_sensitive_term(None) is False
