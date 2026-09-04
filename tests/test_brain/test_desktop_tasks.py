"""Tests for the Brain's desktop-task path (/proofread, /explain)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from tests._helpers import (
    ScriptedLLM,
    agent_brain,
    assert_no_leak,
    ok_response,
    selected_text,
)
from tokenpal.actions.base import ActionResult
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import Brain
from tokenpal.desktop.tasks import task_max_tokens
from tokenpal.llm.base import LLMResponse

FIXTURE = "SECRET-FIXTURE-7731 teh cat sat on teh mat"
REPLY = "The cat sat on the mat.\nChanges:\n- teh -> the (x2)"
PERSONA_LINE = "Fixed it up. It's in the log and nowhere else."


def _brain(
    tmp_path: Path, responses: list[Any],
) -> tuple[Brain, list[str], ScriptedLLM, MemoryStore]:
    memory = MemoryStore(tmp_path / "m.db")
    memory.setup()
    memory.set_chat_log_max_persisted(50)
    llm = ScriptedLLM(responses)
    brain, buf = agent_brain(llm, [], memory)
    return brain, buf, llm, memory


def _patch_capture(monkeypatch: Any, result: Any) -> None:
    monkeypatch.setattr(
        "tokenpal.brain.orchestrator.capture_selection", lambda **_kw: result,
    )


def _forbid_capture(monkeypatch: Any) -> None:
    def _boom(**_kw: Any) -> Any:
        raise AssertionError("capture_selection must not be called")

    monkeypatch.setattr("tokenpal.brain.orchestrator.capture_selection", _boom)


def _patch_consent(monkeypatch: Any, granted: bool) -> None:
    monkeypatch.setattr(
        "tokenpal.brain.orchestrator.require_consent",
        lambda: None if granted else ActionResult(output="Consent needed.", success=False),
    )


async def test_selection_is_read_prompted_and_delivered_unpersisted(
    tmp_path: Path, monkeypatch: Any, caplog: Any,
) -> None:
    caplog.set_level(logging.DEBUG)
    brain, buf, llm, memory = _brain(
        tmp_path, [ok_response(REPLY), ok_response(PERSONA_LINE)],
    )
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(monkeypatch, selected_text(FIXTURE, whole_field=True, truncated=True))

        await brain._handle_desktop_task("proofread", None)

        assert FIXTURE in llm.prompts[0]
        assert '<desktop_content kind="selection" app="TextEdit">' in llm.prompts[0]
        assert "Proofread the text inside <desktop_content>" in llm.prompts[0]
        assert llm.call_kwargs[0]["max_tokens"] == task_max_tokens(len(FIXTURE))

        assert (
            f"> proofread: {len(FIXTURE)} chars from TextEdit "
            "(whole field — nothing was selected) (truncated)" in buf
        )
        assert f"{REPLY} [unpersisted]" in buf
        assert brain._ui_callback.call_args[0][0] == PERSONA_LINE
        # The persona prompt carries nothing that was read.
        assert FIXTURE not in llm.prompts[1]

        assert_no_leak(FIXTURE, lines=buf, caplog_text=caplog.text, memory=memory)
    finally:
        memory.teardown()


async def test_inline_text_skips_the_read_and_consent(
    tmp_path: Path, monkeypatch: Any, caplog: Any,
) -> None:
    caplog.set_level(logging.DEBUG)
    brain, buf, llm, memory = _brain(
        tmp_path, [ok_response(REPLY), ok_response(PERSONA_LINE)],
    )
    try:
        _patch_consent(monkeypatch, False)
        _forbid_capture(monkeypatch)

        await brain._handle_desktop_task("explain", FIXTURE)

        assert f"> explain: {len(FIXTURE)} chars typed" in buf
        assert '<desktop_content kind="typed" app="TokenPal">' in llm.prompts[0]
        assert "Explain the text inside <desktop_content>" in llm.prompts[0]
        assert f"{REPLY} [unpersisted]" in buf
        assert_no_leak(FIXTURE, lines=buf, caplog_text=caplog.text, memory=memory)
    finally:
        memory.teardown()


async def test_missing_consent_refuses_before_any_read(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, llm, memory = _brain(tmp_path, [])
    try:
        _patch_consent(monkeypatch, False)
        _forbid_capture(monkeypatch)

        await brain._handle_desktop_task("proofread", None)

        assert brain._ui_callback.call_args[0][0] == "Consent needed."
        assert llm.prompts == []
        assert buf == []
    finally:
        memory.teardown()


async def test_a_failed_read_shows_its_message_and_calls_no_llm(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, llm, memory = _brain(tmp_path, [])
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(
            monkeypatch,
            ActionResult(
                output="Won't read from that app: it's on the sensitive-app list.",
                success=False,
            ),
        )

        await brain._handle_desktop_task("proofread", None)

        bubble = brain._ui_callback.call_args[0][0]
        assert bubble == "Won't read from that app: it's on the sensitive-app list."
        assert llm.prompts == []
        assert buf == []
    finally:
        memory.teardown()


async def test_sensitive_window_refuses_before_the_read(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, llm, memory = _brain(tmp_path, [])
    try:
        monkeypatch.setattr(
            brain._personality, "check_sensitive_app", lambda _snapshot: True,
        )
        _forbid_capture(monkeypatch)

        await brain._handle_desktop_task("proofread", None)

        assert brain._ui_callback.call_args[0][0] == "Not now — sensitive window is open."
        assert llm.prompts == []
        assert buf == []
    finally:
        memory.teardown()


async def test_empty_reply_aborts_without_promising_an_answer(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, _llm, memory = _brain(tmp_path, [ok_response("   ")])
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(monkeypatch, selected_text(FIXTURE))

        await brain._handle_desktop_task("proofread", None)

        assert brain._ui_callback.call_args[0][0] == (
            "Nothing came back that time — and nothing was saved."
        )
        assert not any("[unpersisted]" in line for line in buf)
    finally:
        memory.teardown()


async def test_sensitive_window_opened_during_the_llm_call_withholds_the_reply(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, llm, memory = _brain(tmp_path, [ok_response(REPLY)])
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(monkeypatch, selected_text(FIXTURE))
        calls = iter([False, True])
        monkeypatch.setattr(
            brain._personality, "check_sensitive_app", lambda _snapshot: next(calls),
        )

        await brain._handle_desktop_task("proofread", None)

        assert len(llm.prompts) == 1
        assert brain._ui_callback.call_args[0][0] == "Not now — sensitive window is open."
        assert not any("[unpersisted]" in line for line in buf)
    finally:
        memory.teardown()


async def test_a_reply_that_hit_the_token_cap_is_marked(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    capped = LLMResponse(
        text=REPLY, tokens_used=1, model_name="t", latency_ms=0, finish_reason="length",
    )
    brain, buf, _llm, memory = _brain(tmp_path, [capped, ok_response(PERSONA_LINE)])
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(monkeypatch, selected_text(FIXTURE))

        await brain._handle_desktop_task("proofread", None)

        assert f"{REPLY}\n… (cut off at the reply limit) [unpersisted]" in buf
    finally:
        memory.teardown()


async def test_an_llm_failure_aborts_and_still_starts_the_cooldown(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, buf, llm, memory = _brain(tmp_path, [])
    try:
        _patch_consent(monkeypatch, True)
        _patch_capture(monkeypatch, selected_text(FIXTURE))

        async def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("server down")

        monkeypatch.setattr(llm, "generate", _boom)
        before = brain._last_comment_time

        await brain._handle_desktop_task("proofread", None)

        assert brain._ui_callback.call_args[0][0] == (
            "Nothing came back that time — and nothing was saved."
        )
        assert brain._last_comment_time > before
    finally:
        memory.teardown()


async def test_no_chat_log_means_no_read_at_all(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    brain, _buf, llm, memory = _brain(tmp_path, [])
    try:
        brain._log_callback = None
        _forbid_capture(monkeypatch)

        await brain._handle_desktop_task("proofread", None)

        assert brain._ui_callback.call_args[0][0] == (
            "No chat log on this overlay — nothing to deliver into."
        )
        assert llm.prompts == []
    finally:
        memory.teardown()


def test_submit_desktop_task_posts_to_queue() -> None:
    brain = Brain.__new__(Brain)
    posts: list[tuple[Any, Any, str]] = []

    def fake_post(queue: Any, item: Any, label: str) -> None:
        posts.append((queue, item, label))

    brain._post_threadsafe = fake_post  # type: ignore[method-assign]
    brain._desktop_task_queue = asyncio.Queue()

    brain.submit_desktop_task("explain", "teh cat")
    assert len(posts) == 1
    assert posts[0][0] is brain._desktop_task_queue
    assert posts[0][1] == ("explain", "teh cat")
    assert posts[0][2] == "desktop task"
