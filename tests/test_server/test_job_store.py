"""Tests for JsonFileJobStore."""

from pathlib import Path

import pytest

from tokenpal.server.job_store import JsonFileJobStore
from tokenpal.server.models import TrainingJob, TrainingStatus


@pytest.fixture
def store(tmp_path: Path) -> JsonFileJobStore:
    return JsonFileJobStore(tmp_path / "jobs")


def _make_job(
    job_id: str = "test-123", status: TrainingStatus = TrainingStatus.QUEUED,
) -> TrainingJob:
    return TrainingJob(
        job_id=job_id,
        status=status,
        wiki="adventure-time",
        character="BMO",
        base_model="google/gemma-2-2b-it",
    )


def test_put_and_get(store: JsonFileJobStore):
    job = _make_job()
    store.put(job)
    loaded = store.get("test-123")
    assert loaded is not None
    assert loaded.job_id == "test-123"
    assert loaded.wiki == "adventure-time"
    assert loaded.character == "BMO"


def test_get_missing_returns_none(store: JsonFileJobStore):
    assert store.get("nonexistent") is None


def test_get_active_returns_running_job(store: JsonFileJobStore):
    store.put(_make_job("done-1", TrainingStatus.COMPLETE))
    store.put(_make_job("active-1", TrainingStatus.TRAINING))
    active = store.get_active()
    assert active is not None
    assert active.job_id == "active-1"


def test_get_active_returns_none_when_all_done(store: JsonFileJobStore):
    store.put(_make_job("done-1", TrainingStatus.COMPLETE))
    store.put(_make_job("done-2", TrainingStatus.FAILED))
    assert store.get_active() is None


def test_list_recent(store: JsonFileJobStore):
    for i in range(5):
        store.put(_make_job(f"job-{i}"))
    recent = store.list_recent(limit=3)
    assert len(recent) == 3


def test_recover_stale_jobs(store: JsonFileJobStore):
    store.put(_make_job("stale-1", TrainingStatus.TRAINING))
    store.put(_make_job("ok-1", TrainingStatus.COMPLETE))
    store.recover_stale_jobs()

    stale = store.get("stale-1")
    assert stale is not None
    assert stale.status == TrainingStatus.FAILED
    assert "Server restarted" in (stale.error or "")

    ok = store.get("ok-1")
    assert ok is not None
    assert ok.status == TrainingStatus.COMPLETE


def test_put_updates_existing(store: JsonFileJobStore):
    job = _make_job()
    store.put(job)
    job.status = TrainingStatus.TRAINING
    job.progress.append("Epoch 1/3")
    store.put(job)
    loaded = store.get("test-123")
    assert loaded is not None
    assert loaded.status == TrainingStatus.TRAINING
    assert "Epoch 1/3" in loaded.progress


def test_json_files_are_human_readable(store: JsonFileJobStore):
    store.put(_make_job())
    path = store._dir / "test-123.json"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert '"job_id": "test-123"' in content
