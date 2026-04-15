"""Tests for the tool catalog."""

from __future__ import annotations

from tokenpal.actions.catalog import DEFAULT_SECTION, LOCAL_SECTION, SECTIONS


def test_default_section_has_four_entries() -> None:
    assert {e.name for e in DEFAULT_SECTION.entries} == {
        "timer",
        "system_info",
        "open_app",
        "do_math",
    }


def test_local_section_lists_all_phase1_tools() -> None:
    assert {e.name for e in LOCAL_SECTION.entries} == {
        "read_file",
        "grep_codebase",
        "git_log",
        "git_diff",
        "git_status",
        "list_processes",
        "memory_query",
    }


def test_local_section_is_frozen_and_consent_empty() -> None:
    for entry in LOCAL_SECTION.entries:
        assert entry.consent_category == ""


def test_sections_tuple_is_ordered() -> None:
    assert SECTIONS[0] is DEFAULT_SECTION
    assert SECTIONS[1] is LOCAL_SECTION


def test_tool_specs_valid() -> None:
    from tokenpal.actions.git_log import GitDiffAction, GitLogAction, GitStatusAction
    from tokenpal.actions.grep_codebase import GrepCodebaseAction
    from tokenpal.actions.list_processes import ListProcessesAction
    from tokenpal.actions.memory_query import MemoryQueryAction
    from tokenpal.actions.read_file import ReadFileAction

    for cls in (
        ReadFileAction,
        GrepCodebaseAction,
        GitLogAction,
        GitDiffAction,
        GitStatusAction,
        ListProcessesAction,
        MemoryQueryAction,
    ):
        spec = cls({}).to_tool_spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == cls.action_name
        assert "parameters" in spec["function"]
        assert spec["function"]["parameters"]["type"] == "object"
