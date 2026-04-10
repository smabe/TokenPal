"""Tests for the slash command dispatcher."""

from __future__ import annotations

from tokenpal.commands import CommandDispatcher, CommandResult


def _make_dispatcher() -> CommandDispatcher:
    d = CommandDispatcher()
    d.register("ping", lambda args: CommandResult("pong"))
    d.register("echo", lambda args: CommandResult(args or "nothing"))
    return d


def test_dispatch_known_command():
    d = _make_dispatcher()
    result = d.dispatch("/ping")
    assert result.message == "pong"


def test_dispatch_with_args():
    d = _make_dispatcher()
    result = d.dispatch("/echo hello world")
    assert result.message == "hello world"


def test_dispatch_no_args():
    d = _make_dispatcher()
    result = d.dispatch("/echo")
    assert result.message == "nothing"


def test_dispatch_unknown_command():
    d = _make_dispatcher()
    result = d.dispatch("/nope")
    assert "Unknown" in result.message
    assert "/nope" in result.message


def test_dispatch_case_insensitive():
    d = _make_dispatcher()
    result = d.dispatch("/PING")
    assert result.message == "pong"


def test_help_text_lists_commands():
    d = _make_dispatcher()
    text = d.help_text()
    assert "/echo" in text
    assert "/ping" in text


def test_dispatch_handler_exception():
    d = CommandDispatcher()
    d.register("boom", lambda args: (_ for _ in ()).throw(ValueError("oops")))
    result = d.dispatch("/boom")
    assert "Error" in result.message
