"""Tests for training routes — submit and poll."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from tokenpal.server.app import create_app
from tokenpal.server.job_store import JsonFileJobStore
from tokenpal.server.models import TrainingJob, TrainingStatus


@pytest.fixture
def app(tmp_path):
    application = create_app(ollama_url="http://fake-ollama:11434")
    application.state.ollama_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    application.state.ollama_url = "http://fake-ollama:11434"
    application.state.ollama_healthy = True
    application.state.job_store = JsonFileJobStore(tmp_path / "jobs")
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_submit_training_job(client):
    target = "tokenpal.server.routes_training.submit_training_job"
    with patch(target, new_callable=AsyncMock) as mock:
        mock.return_value = TrainingJob(
            job_id="test-abc123",
            status=TrainingStatus.QUEUED,
            wiki="adventure-time",
            character="BMO",
            base_model="google/gemma-2-2b-it",
        )
        resp = client.post("/api/v1/train", json={
            "wiki": "adventure-time",
            "character": "BMO",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == "test-abc123"
        assert data["status"] == "queued"


def test_submit_training_conflict(client):
    target = "tokenpal.server.routes_training.submit_training_job"
    with patch(target, new_callable=AsyncMock) as mock:
        mock.side_effect = ValueError("Training already in progress: existing-job")
        resp = client.post("/api/v1/train", json={
            "wiki": "adventure-time",
            "character": "BMO",
        })
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]


def test_submit_validates_wiki_name(client):
    resp = client.post("/api/v1/train", json={
        "wiki": "evil.com/malicious#",
        "character": "BMO",
    })
    assert resp.status_code == 422


def test_submit_validates_character_name(client):
    resp = client.post("/api/v1/train", json={
        "wiki": "adventure-time",
        "character": "$(rm -rf /)",
    })
    assert resp.status_code == 422


def test_poll_training_status(app, client):
    job = TrainingJob(
        job_id="poll-test",
        status=TrainingStatus.TRAINING,
        wiki="adventure-time",
        character="BMO",
        base_model="google/gemma-2-2b-it",
        progress=["Fetching wiki...", "Training epoch 1/3"],
    )
    app.state.job_store.put(job)

    resp = client.get("/api/v1/train/poll-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "training"
    assert len(data["progress"]) == 2
    assert "epoch 1/3" in data["progress"][1]


def test_poll_missing_job(client):
    resp = client.get("/api/v1/train/nonexistent")
    assert resp.status_code == 404


def test_poll_completed_job_has_model_name(app, client):
    job = TrainingJob(
        job_id="done-test",
        status=TrainingStatus.COMPLETE,
        wiki="adventure-time",
        character="BMO",
        base_model="google/gemma-2-2b-it",
        model_name="tokenpal-bmo",
    )
    app.state.job_store.put(job)

    resp = client.get("/api/v1/train/done-test")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == "tokenpal-bmo"


def test_poll_failed_job_has_error(app, client):
    job = TrainingJob(
        job_id="fail-test",
        status=TrainingStatus.FAILED,
        wiki="adventure-time",
        character="BMO",
        base_model="google/gemma-2-2b-it",
        error="CUDA out of memory",
        error_hint="GPU out of memory. Try a smaller base model.",
    )
    app.state.job_store.put(job)

    resp = client.get("/api/v1/train/fail-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "CUDA out of memory" in data["error"]
    assert data["error_hint"] is not None
