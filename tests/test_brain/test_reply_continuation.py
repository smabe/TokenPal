"""Tests for the conversation-reply continuation loop.

When the LLM stops because it hit `max_tokens` (finish_reason="length"),
the brain should fire follow-up calls and concatenate them so the user sees
a complete thought. If even that runs out, the tail is trimmed to the last
sentence boundary and terminated with an ellipsis.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tokenpal.brain.orchestrator import (
    Brain,
    _ends_with_sentence,
    _trim_to_last_sentence,
)
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse


class _ScriptedLLM(AbstractLLMBackend):
    backend_name = "scripted"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, script: list[tuple[str, str | None]]) -> None:
        super().__init__({"max_tokens": 40})
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def setup(self) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def generate(self, prompt: str, max_tokens: int | None = None) -> LLMResponse:
        raise NotImplementedError

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "max_tokens": max_tokens})
        text, reason = self._script.pop(0)
        return LLMResponse(
            text=text,
            tokens_used=10,
            model_name="scripted",
            latency_ms=1.0,
            finish_reason=reason,
        )


def _brain(llm: _ScriptedLLM) -> Brain:
    return Brain(
        senses=[],
        llm=llm,
        ui_callback=MagicMock(),
        personality=PersonalityEngine("You are a test bot."),
    )


class TestSentenceHelpers:
    def test_ends_with_sentence_true(self) -> None:
        assert _ends_with_sentence("All done.")
        assert _ends_with_sentence("Really? ")
        assert _ends_with_sentence('He said "yes."')
        assert _ends_with_sentence("Nice!*")

    def test_ends_with_sentence_false(self) -> None:
        assert not _ends_with_sentence("so we")
        assert not _ends_with_sentence("Making science happen on the Moon so we")
        assert not _ends_with_sentence("")

    def test_trim_to_last_sentence(self) -> None:
        assert _trim_to_last_sentence("One. Two. Thre") == "One. Two."
        assert _trim_to_last_sentence("no terminator here") == ""
        assert _trim_to_last_sentence("Done!") == "Done!"


class TestReplyContinuation:
    @pytest.mark.asyncio
    async def test_no_continuation_when_stop(self) -> None:
        llm = _ScriptedLLM([("Hello there.", "stop")])
        brain = _brain(llm)
        out = await brain._reply_with_continuation(
            [{"role": "user", "content": "hi"}], max_tokens=64,
        )
        assert out == "Hello there."
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_continues_once_when_length(self) -> None:
        llm = _ScriptedLLM([
            ("First half, ", "length"),
            ("second half done.", "stop"),
        ])
        brain = _brain(llm)
        out = await brain._reply_with_continuation(
            [{"role": "user", "content": "hi"}], max_tokens=64,
        )
        assert out == "First half, second half done."
        assert len(llm.calls) == 2
        # The second call includes the partial assistant turn so the model resumes.
        assert llm.calls[1]["messages"][-1] == {
            "role": "assistant", "content": "First half, ",
        }

    @pytest.mark.asyncio
    async def test_caps_continuations_and_trims_tail(self) -> None:
        # Three straight length-truncations → we stop after _MAX_CONTINUATIONS
        # and clip the ragged tail back to the last sentence.
        llm = _ScriptedLLM([
            ("Intro sentence. Partial ", "length"),
            ("more partial ", "length"),
            ("still partial", "length"),
        ])
        brain = _brain(llm)
        out = await brain._reply_with_continuation(
            [{"role": "user", "content": "hi"}], max_tokens=64,
        )
        assert len(llm.calls) == brain._MAX_CONTINUATIONS + 1
        assert out.endswith("…")
        assert out.startswith("Intro sentence.")

    @pytest.mark.asyncio
    async def test_empty_piece_breaks_loop(self) -> None:
        # If the model returns empty text with finish_reason=length, don't
        # loop forever — bail out.
        llm = _ScriptedLLM([("", "length")])
        brain = _brain(llm)
        out = await brain._reply_with_continuation(
            [{"role": "user", "content": "hi"}], max_tokens=64,
        )
        assert out == ""
        assert len(llm.calls) == 1
