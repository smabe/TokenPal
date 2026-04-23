"""Tests for ResearchFollowupAction.

The action wires: consent → brain ref → active followup session TTL/cap →
CloudBackend.followup() call → session update → formatted tool_result.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from tokenpal.actions.research.research_action import ResearchFollowupAction
from tokenpal.brain.research import Source
from tokenpal.brain.research_followup import FollowupSession
from tokenpal.config.schema import CloudLLMConfig, ResearchConfig


def _make_session(**overrides) -> FollowupSession:
    defaults = dict(
        mode="synth",
        model="claude-haiku-4-5",
        sources=[Source(number=1, url="https://example.com", title="x", excerpt="y")],
        messages=[
            {"role": "user", "content": "prompt"},
            {"role": "assistant", "content": "prior answer"},
        ],
        tools=[],
        ttl_s=900,
        max_followups=5,
    )
    defaults.update(overrides)
    return FollowupSession(**defaults)


@pytest.fixture()
def grant_research_consent(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)
    consent_mod.save_consent(
        {consent_mod.Category.RESEARCH_MODE: True},
        path,
    )
    yield


@pytest.fixture()
def stub_cloud_key(monkeypatch: pytest.MonkeyPatch):
    """Return a fixed key from get_cloud_key so the action constructs a backend."""
    import tokenpal.actions.research.research_action as mod
    monkeypatch.setattr(mod, "get_cloud_key", lambda: "sk-ant-test")


def _make_action_with_brain(session: FollowupSession | None) -> ResearchFollowupAction:
    action = ResearchFollowupAction({})
    action._research_config = ResearchConfig(followup_enabled=True, followup_ttl_s=900)
    action._cloud_config = CloudLLMConfig(enabled=True, model="claude-haiku-4-5")
    brain = SimpleNamespace(_active_followup_session=session)
    action._brain_ref = brain
    return action


@pytest.mark.asyncio
async def test_rejects_empty_question() -> None:
    action = _make_action_with_brain(None)
    result = await action.execute(question="")
    assert result.success is False
    assert "empty question" in result.output


@pytest.mark.asyncio
async def test_without_research_consent_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tokenpal.config import consent as consent_mod

    path = tmp_path / "consent.json"
    monkeypatch.setattr(consent_mod, "_default_path", lambda: path)

    action = _make_action_with_brain(_make_session())
    result = await action.execute(question="q")
    assert result.success is False
    assert "research_mode" in result.output


@pytest.mark.asyncio
async def test_no_active_session_errors(grant_research_consent) -> None:
    action = _make_action_with_brain(None)
    result = await action.execute(question="what else?")
    assert result.success is False
    assert "no recent" in result.output.lower()


@pytest.mark.asyncio
async def test_expired_session_errors_and_clears_slot(grant_research_consent) -> None:
    session = _make_session(ttl_s=10)
    session.last_used_at = time.time() - 30  # expired
    action = _make_action_with_brain(session)
    result = await action.execute(question="what else?")
    assert result.success is False
    assert "expired" in result.output.lower()
    # Slot cleared
    assert action._brain_ref._active_followup_session is None


@pytest.mark.asyncio
async def test_over_cap_errors(grant_research_consent) -> None:
    session = _make_session(max_followups=2)
    session.followup_count = 2
    action = _make_action_with_brain(session)
    result = await action.execute(question="what else?")
    assert result.success is False
    assert "cap reached" in result.output.lower()


@pytest.mark.asyncio
async def test_followup_disabled_in_config_errors(grant_research_consent) -> None:
    action = _make_action_with_brain(_make_session())
    action._research_config = ResearchConfig(followup_enabled=False)
    result = await action.execute(question="what else?")
    assert result.success is False
    assert "disabled" in result.output.lower()


@pytest.mark.asyncio
async def test_happy_path_updates_session_and_returns_tool_result(
    grant_research_consent, stub_cloud_key, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    action = _make_action_with_brain(session)

    from tokenpal.actions.research import research_action as mod
    from tokenpal.llm.cloud_backend import FollowupResult

    captured: dict[str, Any] = {}

    def fake_followup(
        self, prior_messages, tools, new_user_turn, *, enable_cache=True,
        max_tokens=3000,
    ):
        captured["prior_messages"] = prior_messages
        captured["tools"] = tools
        captured["new_user_turn"] = new_user_turn
        captured["enable_cache"] = enable_cache
        return FollowupResult(
            text="follow-up answer text",
            messages=[
                *prior_messages,
                {"role": "user", "content": new_user_turn},
                {"role": "assistant", "content": "follow-up answer text"},
            ],
            tokens_used=42,
            cache_read_tokens=2500,
            cache_creation_tokens=0,
            iterations=0,
            latency_ms=900.0,
            finish_reason="stop",
        )

    monkeypatch.setattr(mod.CloudBackend, "followup", fake_followup)

    result = await action.execute(question="I tried X, what else?")
    assert result.success is True
    assert "follow-up answer text" in result.output
    assert "<tool_result" in result.output
    assert "followup=1/5" in result.output
    assert "cache_read=2500" in result.output

    # Session updated: count incremented, messages replaced, telemetry accumulated
    assert session.followup_count == 1
    assert session.total_cache_read_tokens == 2500
    assert len(session.messages) == 4
    assert captured["new_user_turn"] == "I tried X, what else?"
    assert captured["enable_cache"] is True


@pytest.mark.asyncio
async def test_cloud_backend_error_returns_failure_result(
    grant_research_consent, stub_cloud_key, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    action = _make_action_with_brain(session)

    from tokenpal.actions.research import research_action as mod
    from tokenpal.llm.cloud_backend import CloudBackendError

    def fake_followup(self, *args, **kwargs):
        raise CloudBackendError("boom", kind="rate_limit")

    monkeypatch.setattr(mod.CloudBackend, "followup", fake_followup)

    result = await action.execute(question="q")
    assert result.success is False
    assert "rate_limit" in result.output
    # Failed call should NOT have incremented the counter
    assert session.followup_count == 0


@pytest.mark.asyncio
async def test_no_cloud_key_errors(
    grant_research_consent, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    action = _make_action_with_brain(session)
    import tokenpal.actions.research.research_action as mod
    monkeypatch.setattr(mod, "get_cloud_key", lambda: None)

    result = await action.execute(question="q")
    assert result.success is False
    assert "cloud key" in result.output.lower()


@pytest.mark.asyncio
async def test_pins_backend_to_session_model_not_current_config(
    grant_research_consent, stub_cloud_key, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session was opened with Haiku; user swapped to Sonnet mid-session. The
    follow-up must still target Haiku — the cache + tool schema belong to the
    original model.
    """
    session = _make_session(model="claude-haiku-4-5")
    action = _make_action_with_brain(session)
    # Current cloud config now points at Sonnet
    action._cloud_config = CloudLLMConfig(
        enabled=True, model="claude-sonnet-4-6",
    )

    from tokenpal.actions.research import research_action as mod
    from tokenpal.llm.cloud_backend import CloudBackend, FollowupResult

    built_models: list[str] = []
    original_init = CloudBackend.__init__

    def spying_init(self, *args: Any, **kwargs: Any) -> None:
        built_models.append(kwargs.get("model") or (args[1] if len(args) > 1 else ""))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(CloudBackend, "__init__", spying_init)
    monkeypatch.setattr(
        mod.CloudBackend, "followup",
        lambda self, *a, **kw: FollowupResult(
            text="ok", messages=[], tokens_used=1,
            cache_read_tokens=0, cache_creation_tokens=0,
            iterations=0, latency_ms=1.0, finish_reason="stop",
        ),
    )

    await action.execute(question="q")
    assert built_models[-1] == "claude-haiku-4-5"
