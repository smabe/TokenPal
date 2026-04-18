"""Tests for server routes — inference proxy and server info."""

import httpx
import pytest
from fastapi.testclient import TestClient

from tokenpal.server.app import create_app
from tokenpal.server.job_store import JsonFileJobStore


@pytest.fixture
def app(tmp_path):
    """Create a test app with a mock Ollama transport."""
    application = create_app(ollama_url="http://fake-ollama:11434")

    # Replace lifespan-created state for testing
    def mock_transport(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        if "/v1/chat/completions" in path:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "Test response"}}],
                "usage": {"total_tokens": 10},
            })
        if "/v1/models" in path:
            return httpx.Response(200, json={
                "data": [{"id": "gemma4"}, {"id": "tokenpal-bmo"}],
            })
        if path.endswith("/props"):
            return httpx.Response(200, json={
                "default_generation_settings": {"n_ctx": 8192},
                "model_path": "/models/qwen3.gguf",
            })
        if path.endswith("/"):
            return httpx.Response(200, text="Ollama is running")
        return httpx.Response(404)

    application.state.ollama_client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_transport),
    )
    application.state.ollama_url = "http://fake-ollama:11434"
    application.state.ollama_healthy = True
    application.state.job_store = JsonFileJobStore(tmp_path / "jobs")
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_proxy_forwards_chat_completion(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "gemma4",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Test response"


def test_proxy_forwards_models_list(client):
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    models = resp.json()["data"]
    assert len(models) == 2
    assert models[0]["id"] == "gemma4"


def test_proxy_forwards_props(client):
    resp = client.get("/props")
    assert resp.status_code == 200
    assert resp.json()["default_generation_settings"]["n_ctx"] == 8192


def test_proxy_returns_502_when_ollama_down(tmp_path):
    """Proxy returns 502 with actionable hint when Ollama is unreachable."""
    app = create_app(ollama_url="http://localhost:19999")
    # Client that always raises ConnectError
    app.state.ollama_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(httpx.ConnectError("Connection refused")),
        ),
    )
    app.state.ollama_url = "http://localhost:19999"
    app.state.ollama_healthy = False
    app.state.job_store = JsonFileJobStore(tmp_path / "jobs")

    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={
        "model": "gemma4",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 502
    body = resp.json()
    assert "Ollama unreachable" in body["error"]
    assert "hint" in body


def test_server_info_endpoint(client):
    resp = client.get("/api/v1/server/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server_version"] == "0.1.0"
    assert data["api_version"] == 1
    assert data["ollama_healthy"] is True
    assert data["active_training_job"] is None
    assert "hf_token_set" in data


def test_server_info_shows_active_job(app, client, tmp_path):
    from tokenpal.server.models import TrainingJob, TrainingStatus
    job = TrainingJob(
        job_id="bmo-001", status=TrainingStatus.TRAINING,
        wiki="adventure-time", character="BMO",
        base_model="google/gemma-2-2b-it",
    )
    app.state.job_store.put(job)
    resp = client.get("/api/v1/server/info")
    assert resp.json()["active_training_job"] == "bmo-001"
