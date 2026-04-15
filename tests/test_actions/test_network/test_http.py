"""Tests for the shared _http helpers."""

from __future__ import annotations

from tokenpal.actions.network import _http


def test_wrap_result_envelope_contains_tool_name() -> None:
    out = _http.wrap_result("foo", "hello world")
    assert out.startswith('<tool_result tool="foo">')
    assert out.endswith("</tool_result>")
    assert "hello world" in out


def test_wrap_result_scrubs_sensitive_lines(monkeypatch) -> None:
    def fake_sensitive(text: str) -> bool:
        return "BAD" in (text or "")

    monkeypatch.setattr(_http, "contains_sensitive_term", fake_sensitive)
    out = _http.wrap_result("foo", "line one\nBAD line\nline three")
    assert "[filtered]" in out
    assert "BAD line" not in out
    assert "line one" in out
    assert "line three" in out
