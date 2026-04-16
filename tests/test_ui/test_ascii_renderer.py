"""Tests for ascii_renderer helpers: markup fix, Rich color remap, HTML strip."""

from __future__ import annotations

from tokenpal.ui.ascii_renderer import _fix_markup


def test_fix_markup_strips_html_tags() -> None:
    # Issue #23: LLM frames sometimes leak <u>/</u>/<b> angle-bracket tags.
    lines = [
        "[cyan]greeting[/cyan]",
        "</u>fake</u>",
        "<b>bold</b> mid <i>italic</i>",
        "clean",
    ]
    out = _fix_markup(lines)
    assert "</u>" not in out[1]
    assert "<b>" not in out[2]
    assert "</b>" not in out[2]
    assert "<i>" not in out[2]
    assert "</i>" not in out[2]
    assert out[3] == "clean"


def test_fix_markup_preserves_bracket_markup() -> None:
    lines = ["[red]hello[/red]", "[#ff0000]hex[/#ff0000]"]
    assert _fix_markup(lines) == lines


def test_fix_markup_remaps_rich_only_colors() -> None:
    out = _fix_markup(["[silver]dim[/silver]"])
    assert "#c0c0c0" in out[0]
    assert "silver" not in out[0]


def test_fix_markup_mixed_html_and_bracket() -> None:
    out = _fix_markup(["[green]█[/green] </u>tail"])
    assert "[green]" in out[0]
    assert "[/green]" in out[0]
    assert "</u>" not in out[0]
