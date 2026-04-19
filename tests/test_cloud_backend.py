"""Tests for tokenpal/llm/cloud_backend.py — Anthropic API wrapper.

All tests mock the Anthropic client. Nothing hits the real API.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tokenpal.llm.cloud_backend import (
    ALLOWED_MODELS,
    CloudBackend,
    CloudBackendError,
    _extract_text,
    _harden_schema_for_anthropic,
    _map_stop_reason,
)


def _fake_message(
    text: str, stop_reason: str = "end_turn", output_tokens: int = 42,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(output_tokens=output_tokens, input_tokens=100),
    )


class _FakeClient:
    def __init__(self, *, result: Any = None, raise_on_call: Exception | None = None) -> None:
        self._result = result
        self._raise = raise_on_call
        self.messages = SimpleNamespace(create=self._create)
        self.last_kwargs: dict[str, Any] = {}

    def _create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._raise is not None:
            raise self._raise
        return self._result


@pytest.fixture()
def fake_anthropic(monkeypatch: pytest.MonkeyPatch):
    """Replace anthropic.Anthropic with a shim; individual tests set behavior."""
    import anthropic

    holder: dict[str, _FakeClient] = {}

    def factory(**kwargs: Any) -> _FakeClient:
        client = holder["client"]
        holder["last_init_kwargs"] = kwargs
        return client

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    return holder


def test_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        CloudBackend(api_key="sk-ant-x", model="claude-opus-3")


def test_allowlist_is_frozen() -> None:
    assert "claude-haiku-4-5" in ALLOWED_MODELS
    assert "claude-sonnet-4-6" in ALLOWED_MODELS
    assert "claude-opus-4-7" in ALLOWED_MODELS


def test_happy_path_returns_text_and_tokens(fake_anthropic: dict[str, Any]) -> None:
    fake_anthropic["client"] = _FakeClient(
        result=_fake_message('{"kind":"factual"}', output_tokens=17),
    )
    b = CloudBackend(api_key="sk-ant-test", model="claude-haiku-4-5", timeout_s=5.0)
    resp = b.synthesize("prompt", max_tokens=500)
    assert resp.text == '{"kind":"factual"}'
    assert resp.tokens_used == 17
    assert resp.model_name == "claude-haiku-4-5"
    assert resp.finish_reason == "stop"
    # init kwargs propagate
    assert fake_anthropic["last_init_kwargs"]["api_key"] == "sk-ant-test"
    assert fake_anthropic["last_init_kwargs"]["timeout"] == 5.0


def test_synthesize_passes_json_schema_as_output_config(
    fake_anthropic: dict[str, Any]
) -> None:
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test")
    schema = {"type": "object", "properties": {"kind": {"type": "string"}}}
    b.synthesize("prompt", max_tokens=100, json_schema=schema)
    sent = client.last_kwargs["output_config"]
    assert sent["format"]["type"] == "json_schema"
    # Schema is hardened at send-time - properties preserved, additionalProperties
    # added. See test_synthesize_hardens_schema_before_send for the invariant.
    assert sent["format"]["schema"]["properties"] == schema["properties"]
    assert sent["format"]["schema"]["additionalProperties"] is False


def test_synthesize_omits_output_config_when_no_schema(fake_anthropic: dict[str, Any]) -> None:
    client = _FakeClient(result=_fake_message("plain"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test")
    b.synthesize("prompt", max_tokens=100)
    assert "output_config" not in client.last_kwargs


def test_synthesize_haiku_does_not_send_thinking(
    fake_anthropic: dict[str, Any]
) -> None:
    """Haiku 4.5 errors on thinking param - must be absent."""
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test", model="claude-haiku-4-5")
    b.synthesize("p", max_tokens=500)
    assert "thinking" not in client.last_kwargs


def test_synthesize_sonnet_enables_adaptive_thinking(
    fake_anthropic: dict[str, Any]
) -> None:
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test", model="claude-sonnet-4-6")
    b.synthesize("p", max_tokens=500)
    assert client.last_kwargs["thinking"] == {"type": "adaptive"}


def test_synthesize_opus_enables_adaptive_thinking(
    fake_anthropic: dict[str, Any]
) -> None:
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test", model="claude-opus-4-7")
    b.synthesize("p", max_tokens=500)
    assert client.last_kwargs["thinking"] == {"type": "adaptive"}


def test_auth_error_raises_cloud_backend_error_with_auth_kind(
    fake_anthropic: dict[str, Any],
) -> None:
    import anthropic
    err = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
    Exception.__init__(err, "invalid api key")
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-bad")
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "auth"


def test_permission_denied_credit_balance_mapped_to_no_credit(
    fake_anthropic: dict[str, Any]
) -> None:
    import anthropic
    err = anthropic.PermissionDeniedError.__new__(anthropic.PermissionDeniedError)
    Exception.__init__(err, "Your credit balance is too low to access the Claude API")
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-unfunded")
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "no_credit"


def test_permission_denied_other_mapped_to_permission(fake_anthropic: dict[str, Any]) -> None:
    import anthropic
    err = anthropic.PermissionDeniedError.__new__(anthropic.PermissionDeniedError)
    Exception.__init__(err, "model requires workspace upgrade")
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-x")
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "permission"


def test_rate_limit_maps_to_rate_limit_kind(fake_anthropic: dict[str, Any]) -> None:
    import anthropic
    err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    Exception.__init__(err, "slow down")
    err.response = SimpleNamespace(headers={"retry-after": "30"})
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-x")
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "rate_limit"
    assert exc_info.value.retry_after == 30.0


def test_timeout_maps_to_timeout_kind(fake_anthropic: dict[str, Any]) -> None:
    import anthropic
    err = anthropic.APITimeoutError.__new__(anthropic.APITimeoutError)
    Exception.__init__(err, "timeout")
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-x", timeout_s=2.0)
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "timeout"


def test_connection_error_maps_to_network_kind(fake_anthropic: dict[str, Any]) -> None:
    import anthropic
    err = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    Exception.__init__(err, "dns fail")
    fake_anthropic["client"] = _FakeClient(raise_on_call=err)
    b = CloudBackend(api_key="sk-ant-x")
    with pytest.raises(CloudBackendError) as exc_info:
        b.synthesize("p")
    assert exc_info.value.kind == "network"


def test_extract_text_joins_text_blocks_only() -> None:
    msg = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="hello "),
        SimpleNamespace(type="tool_use", input={"x": 1}),
        SimpleNamespace(type="text", text="world"),
    ])
    assert _extract_text(msg) == "hello world"


def test_extract_text_empty_content() -> None:
    assert _extract_text(SimpleNamespace(content=None)) == ""
    assert _extract_text(SimpleNamespace(content=[])) == ""


def test_harden_schema_injects_additional_properties_false() -> None:
    """Anthropic 400s a schema without additionalProperties on any object.
    We must inject false on every nested object, including items + combinators.
    """
    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            "verdict": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        },
        "required": ["kind"],
    }
    hardened = _harden_schema_for_anthropic(schema)
    assert hardened["additionalProperties"] is False
    assert hardened["properties"]["picks"]["items"]["additionalProperties"] is False
    assert hardened["properties"]["verdict"]["additionalProperties"] is False
    # Original schema must not be mutated
    assert "additionalProperties" not in schema


def test_harden_schema_preserves_explicit_true() -> None:
    """If someone set additionalProperties: true, don't overwrite it."""
    schema = {"type": "object", "additionalProperties": True}
    assert _harden_schema_for_anthropic(schema)["additionalProperties"] is True


def test_harden_schema_walks_anyof_combinators() -> None:
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"type": "string"},
        ],
    }
    hardened = _harden_schema_for_anthropic(schema)
    assert hardened["anyOf"][0]["additionalProperties"] is False


def test_synthesize_hardens_schema_before_send(fake_anthropic: dict[str, Any]) -> None:
    """End-to-end: the schema sent to Anthropic has the required field."""
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test")
    # Schema without additionalProperties - matches our local SYNTH_SCHEMA shape
    b.synthesize("p", max_tokens=100, json_schema={
        "type": "object",
        "properties": {"x": {"type": "string"}},
    })
    sent = client.last_kwargs["output_config"]["format"]["schema"]
    assert sent["additionalProperties"] is False


def test_stop_reason_mapping() -> None:
    assert _map_stop_reason("end_turn") == "stop"
    assert _map_stop_reason("stop_sequence") == "stop"
    assert _map_stop_reason("max_tokens") == "length"
    assert _map_stop_reason(None) is None
    assert _map_stop_reason("refusal") == "refusal"  # passthrough for unknown
