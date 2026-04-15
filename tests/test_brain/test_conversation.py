"""Tests for multi-turn conversation context and the conversational prompt path."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from tokenpal.brain.orchestrator import Brain, ConversationSession
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.config.schema import ConversationConfig
from tokenpal.llm.base import AbstractLLMBackend, LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine() -> PersonalityEngine:
    return PersonalityEngine("You are a test bot.")


class _MockLLM(AbstractLLMBackend):
    backend_name = "mock"
    platforms = ("darwin", "linux", "windows")

    def __init__(self, responses: list[str] | None = None) -> None:
        super().__init__({"max_tokens": 40})
        self._responses = list(responses or ["Test response that is long enough."])
        self.calls: list[dict[str, Any]] = []

    async def setup(self) -> None:
        pass

    async def generate(self, prompt: str, max_tokens: int = 256) -> LLMResponse:
        text = self._responses.pop(0) if self._responses else ""
        return LLMResponse(text=text, tokens_used=10, model_name="mock", latency_ms=5.0)

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 256,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "tools": tools})
        text = self._responses.pop(0) if self._responses else ""
        return LLMResponse(text=text, tokens_used=10, model_name="mock", latency_ms=5.0)

    async def teardown(self) -> None:
        pass


def _make_brain(
    llm: _MockLLM | None = None,
    conversation: ConversationConfig | None = None,
) -> Brain:
    personality = PersonalityEngine("You are a test bot.")
    return Brain(
        senses=[],
        llm=llm or _MockLLM(),
        ui_callback=MagicMock(),
        personality=personality,
        conversation=conversation,
    )


# ---------------------------------------------------------------------------
# ConversationSession unit tests
# ---------------------------------------------------------------------------

class TestConversationSession:
    def test_new_session_is_not_active(self):
        s = ConversationSession()
        assert not s.is_active
        assert s.turn_count == 0

    def test_add_turns_makes_active(self):
        s = ConversationSession()
        s.add_user_turn("hello")
        s.add_assistant_turn("hi there, how are you doing?")
        assert s.is_active
        assert s.turn_count == 1

    def test_history_alternates(self):
        s = ConversationSession()
        s.add_user_turn("hello")
        s.add_assistant_turn("hi back to you buddy")
        s.add_user_turn("how are you?")
        s.add_assistant_turn("doing great thanks")
        assert len(s.history) == 4
        assert s.history[0]["role"] == "user"
        assert s.history[1]["role"] == "assistant"
        assert s.history[2]["role"] == "user"
        assert s.history[3]["role"] == "assistant"

    def test_cap_drops_oldest_pair(self):
        s = ConversationSession(max_turns=2)
        # Fill to cap: 2 turn pairs = 4 messages
        s.add_user_turn("u1")
        s.add_assistant_turn("a1")
        s.add_user_turn("u2")
        s.add_assistant_turn("a2")
        assert len(s.history) == 4

        # Add a 3rd pair — oldest should be evicted
        s.add_user_turn("u3")
        s.add_assistant_turn("a3")
        assert len(s.history) == 4
        assert s.history[0]["content"] == "u2"
        assert s.history[1]["content"] == "a2"

    def test_timeout_expires_session(self):
        s = ConversationSession(timeout_s=0.01)
        s.add_user_turn("hello")
        s.add_assistant_turn("hi there, nice to meet you")
        time.sleep(0.02)
        assert s.is_expired
        assert not s.is_active

    def test_user_turn_resets_timeout(self):
        s = ConversationSession(timeout_s=0.05)
        s.add_user_turn("hello")
        s.add_assistant_turn("hi there, nice to meet you")
        time.sleep(0.03)
        # Not expired yet
        assert not s.is_expired
        # New user turn resets the clock
        s.add_user_turn("still here?")
        time.sleep(0.03)
        assert not s.is_expired

    def test_turn_count_only_counts_assistant(self):
        s = ConversationSession()
        s.add_user_turn("u1")
        assert s.turn_count == 0
        s.add_assistant_turn("a1")
        assert s.turn_count == 1
        s.add_user_turn("u2")
        assert s.turn_count == 1
        s.add_assistant_turn("a2")
        assert s.turn_count == 2


# ---------------------------------------------------------------------------
# PersonalityEngine conversation methods
# ---------------------------------------------------------------------------

class TestPersonalityConversation:
    def test_build_conversation_prompt_contains_user_message(self):
        engine = _make_engine()
        prompt = engine.build_conversation_prompt("hello there", "App: VS Code")
        assert 'User says: "hello there"' in prompt
        assert "VS Code" in prompt

    def test_build_conversation_prompt_contains_mood(self):
        engine = _make_engine()
        prompt = engine.build_conversation_prompt("test", "App: Chrome")
        assert "mood" in prompt.lower()

    def test_build_conversation_prompt_no_silent_instruction(self):
        engine = _make_engine()
        prompt = engine.build_conversation_prompt("test", "")
        assert "[SILENT]" not in prompt

    def test_build_conversation_system_message_contains_identity(self):
        engine = _make_engine()
        msg = engine.build_conversation_system_message()
        assert "Respond in character" in msg
        assert "top priority" in msg

    def test_build_conversation_system_message_finetuned(self):
        engine = _make_engine()
        engine._finetuned_model = "test-model"
        msg = engine.build_conversation_system_message()
        assert "Stay in your character voice" in msg
        # Finetuned path should NOT include identity block
        assert "test bot" not in msg

    def test_build_context_injection(self):
        engine = _make_engine()
        msg = engine.build_context_injection("App: Safari, CPU 5%")
        assert "Safari" in msg
        assert "CPU 5%" in msg

    def test_filter_conversation_allows_short_responses(self):
        engine = _make_engine()
        assert engine.filter_conversation_response("Never.") == "Never."

    def test_filter_conversation_allows_longer_responses(self):
        engine = _make_engine()
        text = "Sure, I can help with that, but honestly you should have figured this out yourself by now."
        result = engine.filter_conversation_response(text)
        assert result is not None
        assert len(result) > 70

    def test_filter_conversation_does_not_truncate(self):
        """Char capping moved to orchestrator (uses effective max_tokens)."""
        engine = _make_engine()
        text = "A" * 600
        result = engine.filter_conversation_response(text)
        assert result is not None
        assert len(result) == 600

    def test_filter_conversation_strips_markdown(self):
        engine = _make_engine()
        result = engine.filter_conversation_response("*sighs* Fine, whatever.")
        assert result is not None
        assert "*" not in result

    def test_filter_conversation_rejects_empty(self):
        engine = _make_engine()
        assert engine.filter_conversation_response("") is None
        assert engine.filter_conversation_response("   ") is None
        assert engine.filter_conversation_response("Hi") is None  # 2 chars < 5

    def test_filter_conversation_rejects_drift(self):
        engine = _make_engine()
        # Thai drift bubble as actually observed with gemma4:26b
        assert engine.filter_conversation_response(
            "**(ในลังจะหาทาง ท้อน สืบ)**",
        ) is None
        # Markdown meta header
        assert engine.filter_conversation_response(
            "**Analyze the German/Formatting Parts:**",
        ) is None
        # Chain-of-thought leak
        assert engine.filter_conversation_response(
            "I cannot provide a definitive, contextually accurate answer.",
        ) is None

    def test_filter_response_rejects_drift(self):
        engine = _make_engine()
        assert engine.filter_response(
            "ยังไงก็อยู่ในกลุ่มของ 23. 06. 2024 ครับ.",
        ) is None
        assert engine.filter_response(
            "**Analyze the German/Formatting Parts:**",
        ) is None

    def test_filter_response_allows_clean_english(self):
        engine = _make_engine()
        result = engine.filter_response(
            "Oh hey, another window — how surprising. Nice job.",
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Brain integration tests
# ---------------------------------------------------------------------------

class TestBrainConversation:
    async def test_handle_user_input_creates_session(self):
        llm = _MockLLM(["That's a great question my friend."])
        brain = _make_brain(llm=llm)

        assert brain._conversation is None
        await brain._handle_user_input("hello")
        assert brain._conversation is not None
        assert brain._conversation.is_active

    async def test_handle_user_input_builds_messages_array(self):
        llm = _MockLLM(["Response one that is plenty long.", "Response two that is also long enough."])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("first message")
        await brain._handle_user_input("second message")

        # The second call should have history from the first exchange
        last_call = llm.calls[-1]
        messages = last_call["messages"]

        # Should have: system, user1, assistant1, system(context), user2
        roles = [m["role"] for m in messages]
        assert roles[0] == "system"  # persona
        assert "user" in roles
        assert "assistant" in roles

        # The history should contain the first exchange
        contents = [m["content"] for m in messages]
        assert "first message" in contents
        assert "Response one that is plenty long." in contents
        assert "second message" in contents

    async def test_handle_user_input_sensitive_app_clears_session(self):
        llm = _MockLLM(["Hello there buddy pal.", "Another response here."])
        brain = _make_brain(llm=llm)
        ui = brain._ui_callback

        # Start a conversation
        await brain._handle_user_input("hey")
        assert brain._conversation is not None

        # Inject sensitive app into context
        brain._context.ingest([
            MagicMock(
                sense_name="app_awareness",
                summary="App: 1Password",
                confidence=1.0,
                timestamp=time.monotonic(),
                changed_from=None,
            )
        ])

        await brain._handle_user_input("what's my password?")
        # Session should be cleared
        assert brain._conversation is None
        # Should have shown the canned response
        ui.assert_called()
        last_text = ui.call_args[0][0]
        assert "look away" in last_text.lower()

    async def test_observation_suppressed_during_conversation(self):
        llm = _MockLLM(["Hello there buddy pal."])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("hello")
        assert brain._in_conversation
        assert not brain._should_comment()
        assert not brain._should_freeform()

    async def test_session_expires_and_observations_resume(self):
        config = ConversationConfig(timeout_s=0.01)
        llm = _MockLLM(["Hello there buddy pal."])
        brain = _make_brain(llm=llm, conversation=config)

        await brain._handle_user_input("hello")
        assert brain._in_conversation
        time.sleep(0.02)
        assert not brain._in_conversation

    async def test_filtered_response_records_placeholder(self):
        # Response too short — will be filtered
        llm = _MockLLM(["No."])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("hello")
        assert brain._conversation is not None
        # Should have user turn + placeholder assistant turn
        assert len(brain._conversation.history) == 2
        assert brain._conversation.history[1]["content"] == "[no response]"

    async def test_failed_generation_removes_user_turn(self):
        class _FailLLM(_MockLLM):
            async def generate_with_tools(self, **kwargs: Any) -> LLMResponse:
                raise RuntimeError("LLM down")

        brain = _make_brain(llm=_FailLLM())

        await brain._handle_user_input("hello")
        # Session should exist but history should be empty (user turn removed)
        assert brain._conversation is not None
        assert len(brain._conversation.history) == 0

    async def test_long_response_truncated_at_effective_cap(self):
        """Orchestrator caps conversation output at
        effective_max_tokens * 4 * (MAX_CONTINUATIONS + 1) chars — the
        continuation loop gets the full budget before the safety clip kicks in."""
        # 4000 chars of clean English — no periods so filter_conversation_response
        # returns it unchanged.
        long_text = ("A" * 99 + " ") * 40
        llm = _MockLLM([long_text])
        # Pin conversation budget to 100 tokens → cap = 100 * 4 * 3 = 1200 chars.
        config = ConversationConfig(max_response_tokens=100)
        brain = _make_brain(llm=llm, conversation=config)

        await brain._handle_user_input("tell me a story")

        assert brain._conversation is not None
        assistant_turn = brain._conversation.history[-1]["content"]
        expected_cap = 100 * 4 * (brain._MAX_CONTINUATIONS + 1)
        assert assistant_turn.endswith("...")
        assert len(assistant_turn) == expected_cap

    async def test_effective_conv_max_tokens_uses_pin_when_set(self):
        config = ConversationConfig(max_response_tokens=250)
        brain = _make_brain(conversation=config)
        assert brain._effective_conv_max_tokens() == 250

    async def test_effective_conv_max_tokens_falls_back_to_300(self):
        # max_response_tokens = 0 (default) and mock LLM has no derived_max_tokens.
        brain = _make_brain()
        assert brain._effective_conv_max_tokens() == 300

    async def test_effective_conv_max_tokens_uses_derived_when_unpinned(self):
        class _LLMWithDerived(_MockLLM):
            derived_max_tokens = 480

        brain = _make_brain(llm=_LLMWithDerived(["Hi there buddy pal."]))
        assert brain._effective_conv_max_tokens() == 480

    async def test_reset_conversation_clears_session(self):
        llm = _MockLLM(["Hello there buddy pal."])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("hello")
        assert brain._conversation is not None
        brain._clear_conversation()
        assert brain._conversation is None

    async def test_conversation_config_wired_through(self):
        config = ConversationConfig(max_turns=3, timeout_s=60.0)
        brain = _make_brain(conversation=config)

        assert brain._conv_config.max_turns == 3
        assert brain._conv_config.timeout_s == 60.0

    async def test_three_turn_conversation_sends_full_history(self):
        """Simulate the exact Bender scenario from the logs.

        Turn 1: "where are we going to party?"
        Turn 2: "let's hit the casino"
        Turn 3: "who's coming?"

        On turn 3, the LLM must receive turns 1+2 in the messages array,
        so it knows "who's coming?" refers to the party/casino plan.
        """
        llm = _MockLLM([
            "Anywhere with cheap booze and zero rules, meatbag!",
            "Casino! Now you're speaking my language, pal.",
            "Just us, meatbag. Fry's too broke for this.",
        ])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("where are we going to party?")
        await brain._handle_user_input("let's hit the casino")
        await brain._handle_user_input("who's coming?")

        # Verify the LLM received all 3 calls
        assert len(llm.calls) == 3

        # The critical check: turn 3's messages array must contain
        # the full conversation history so the LLM can connect
        # "who's coming?" to the party/casino context
        turn3_messages = llm.calls[2]["messages"]
        turn3_contents = [m["content"] for m in turn3_messages]
        turn3_roles = [m["role"] for m in turn3_messages]

        # System message is first
        assert turn3_roles[0] == "system"

        # All prior user messages are present
        assert "where are we going to party?" in turn3_contents
        assert "let's hit the casino" in turn3_contents
        assert "who's coming?" in turn3_contents

        # All prior assistant responses are present
        assert "Anywhere with cheap booze and zero rules, meatbag!" in turn3_contents
        assert "Casino! Now you're speaking my language, pal." in turn3_contents

        # Messages alternate correctly: system, user, assistant, user, assistant, system(ctx), user
        non_system = [(r, c) for r, c in zip(turn3_roles, turn3_contents) if r != "system"]
        assert non_system[0] == ("user", "where are we going to party?")
        assert non_system[1] == ("assistant", "Anywhere with cheap booze and zero rules, meatbag!")
        assert non_system[2] == ("user", "let's hit the casino")
        assert non_system[3] == ("assistant", "Casino! Now you're speaking my language, pal.")
        assert non_system[4] == ("user", "who's coming?")

        # Verify the internal session state matches
        assert brain._conversation is not None
        assert brain._conversation.turn_count == 3
        assert len(brain._conversation.history) == 6  # 3 user + 3 assistant

    async def test_turn1_has_no_history(self):
        """First message should have system + context + user, no history."""
        llm = _MockLLM(["Hello there, what do you want?"])
        brain = _make_brain(llm=llm)

        await brain._handle_user_input("hey bender")

        turn1_messages = llm.calls[0]["messages"]
        turn1_roles = [m["role"] for m in turn1_messages]

        # Should be: system, system(context), user — no assistant turns
        assert "assistant" not in turn1_roles
        assert turn1_roles.count("user") == 1
        assert turn1_messages[-1]["content"] == "hey bender"
