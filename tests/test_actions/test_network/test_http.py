"""Tests for the shared _http helpers."""

from __future__ import annotations

from tokenpal.actions.network import _base, _http
from tokenpal.util import untrusted_text


def test_wrap_result_envelope_contains_tool_name() -> None:
    out = _http.wrap_result("foo", "hello world")
    assert out.startswith('<tool_result tool="foo">')
    assert out.endswith("</tool_result>")
    assert "hello world" in out


def test_wrap_result_scrubs_sensitive_lines(monkeypatch) -> None:
    def fake_sensitive(text: str) -> bool:
        return "BAD" in (text or "")

    monkeypatch.setattr(untrusted_text, "contains_sensitive_term", fake_sensitive)
    out = _http.wrap_result("foo", "line one\nBAD line\nline three")
    assert "[filtered]" in out
    assert "BAD line" not in out
    assert "line one" in out
    assert "line three" in out


def test_consent_error_message_unchanged() -> None:
    """Pins the copy all 13 network tools show; base.consent_error owns it now."""
    assert _base.consent_error().output == (
        "Tool requires 'web fetches' consent. Open /consent to grant it."
    )
