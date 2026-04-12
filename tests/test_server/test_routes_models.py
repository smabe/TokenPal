"""Tests for model management routes."""

import httpx
import pytest
from fastapi.testclient import TestClient

from tokenpal.server.app import create_app
from tokenpal.server.job_store import JsonFileJobStore


@pytest.fixture
def app(tmp_path):
    def mock_transport(request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        if "/api/tags" in path:
            return httpx.Response(200, json={
                "models": [
                    {"name": "gemma4", "size": 5000000000, "modified_at": "2026-04-01"},
                    {"name": "tokenpal-bmo", "size": 3000000000},
                ],
            })
        if "/api/pull" in path:
            return httpx.Response(200, json={"status": "success"})
        if path.endswith("/"):
            return httpx.Response(200, text="Ollama is running")
        return httpx.Response(404)

    application = create_app(ollama_url="http://fake-ollama:11434")
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


def test_list_models(client):
    resp = client.get("/api/v1/models/list")
    assert resp.status_code == 200
    models = resp.json()
    assert len(models) == 2
    assert models[0]["name"] == "gemma4"
    assert models[0]["size"] == 5000000000
    assert models[1]["name"] == "tokenpal-bmo"


def test_pull_model(client):
    resp = client.post("/api/v1/models/pull", json={"model": "llama3:8b"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["model"] == "llama3:8b"


def test_pull_model_validates_name(client):
    resp = client.post("/api/v1/models/pull", json={"model": "../../../etc/passwd"})
    assert resp.status_code == 422


def test_pull_model_rejects_empty(client):
    resp = client.post("/api/v1/models/pull", json={"model": ""})
    assert resp.status_code == 422
