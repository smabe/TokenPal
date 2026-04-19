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


def test_synthesize_passes_json_schema_as_output_config(fake_anthropic: dict[str, Any]) -> None:
    client = _FakeClient(result=_fake_message("{}"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test")
    schema = {"type": "object", "properties": {"kind": {"type": "string"}}}
    b.synthesize("prompt", max_tokens=100, json_schema=schema)
    assert client.last_kwargs["output_config"] == {
        "format": {"type": "json_schema", "schema": schema},
    }


def test_synthesize_omits_output_config_when_no_schema(fake_anthropic: dict[str, Any]) -> None:
    client = _FakeClient(result=_fake_message("plain"))
    fake_anthropic["client"] = client
    b = CloudBackend(api_key="sk-ant-test")
    b.synthesize("prompt", max_tokens=100)
    assert "output_config" not in client.last_kwargs


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


def test_stop_reason_mapping() -> None:
    assert _map_stop_reason("end_turn") == "stop"
    assert _map_stop_reason("stop_sequence") == "stop"
    assert _map_stop_reason("max_tokens") == "length"
    assert _map_stop_reason(None) is None
    assert _map_stop_reason("refusal") == "refusal"  # passthrough for unknown
