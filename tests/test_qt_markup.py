"""Rich-markup → Qt-segment parser.

Voice-specific buddy art is Rich-markup, and our ``paintEvent`` has
to turn ``[#ffffff]text[/]`` into a list of ``(text, color)`` segments
so each one can be drawn with its own ``QPen``. Before this fix, tags
rendered as literal text.
"""

from __future__ import annotations

from tokenpal.ui.qt.markup import Segment, parse_markup, stripped_text


def test_plain_text_becomes_single_uncolored_segment() -> None:
    assert parse_markup("hello") == [Segment("hello", None)]


def test_single_color_span() -> None:
    segs = parse_markup("[#ff0000]red text[/]")
    assert segs == [Segment("red text", "#ff0000")]


def test_mixed_colored_and_plain_runs() -> None:
    segs = parse_markup("pre[#00ff00]mid[/]post")
    assert segs == [
        Segment("pre", None),
        Segment("mid", "#00ff00"),
        Segment("post", None),
    ]


def test_nested_tags_use_color_stack() -> None:
    segs = parse_markup("[#111111]a[#222222]b[/]c[/]")
    assert segs == [
        Segment("a", "#111111"),
        Segment("b", "#222222"),
        Segment("c", "#111111"),
    ]


def test_unknown_tag_passes_through_as_literal_text() -> None:
    # [foo] isn't a hex color and isn't [/], so it's left as text —
    # don't swallow content we can't recognize.
    segs = parse_markup("hi [foo] world")
    assert segs == [Segment("hi [foo] world", None)]


def test_dangling_close_tag_is_ignored() -> None:
    """[/] with nothing to close shouldn't pop past the root."""
    segs = parse_markup("[/]just text[/]")
    assert segs == [Segment("just text", None)]


def test_empty_spans_are_dropped() -> None:
    segs = parse_markup("[#aaaaaa][/][#bbbbbb]x[/]")
    assert segs == [Segment("x", "#bbbbbb")]


def test_stripped_text_removes_all_tags() -> None:
    line = "[#ff0000]hello[/] [unknown]world[/]"
    assert stripped_text(line) == "hello world"


def test_stripped_text_idempotent_on_plain_line() -> None:
    assert stripped_text("nothing to strip") == "nothing to strip"


def test_real_voice_art_line() -> None:
    """A line from an actual voice frame (color wraps a run of glyphs)."""
    line = r"[#ffffff]░░░░░░[/][#fdfd96]▓▓▓[/]"
    segs = parse_markup(line)
    assert segs == [
        Segment("░░░░░░", "#ffffff"),
        Segment("▓▓▓", "#fdfd96"),
    ]
    assert stripped_text(line) == "░░░░░░▓▓▓"
