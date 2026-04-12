"""Tests for the training worker — mocks all heavy training imports."""

from unittest.mock import MagicMock, patch

import pytest

from tokenpal.server.models import TrainingJob, TrainingStatus
from tokenpal.server.worker import _run_pipeline

# Patch targets: the source modules where the functions are defined,
# because worker.py imports them lazily inside _run_pipeline().
_WIKI = "tokenpal.tools.train_voice.train_from_wiki"
_PREP = "tokenpal.tools.dataset_prep.prepare_dataset"
_SYS_PROMPT = "tokenpal.tools.dataset_prep.build_system_prompt"
_CHECK_GPU = "tokenpal.tools.finetune_voice._check_gpu"
_COUNT = "tokenpal.tools.finetune_voice._count_lines"
_AUTO_TUNE = "tokenpal.tools.finetune_voice.auto_tune"
_SETUP = "tokenpal.tools.finetune_voice.setup_model"
_TRAIN = "tokenpal.tools.finetune_voice.train"
_MERGE = "tokenpal.tools.finetune_voice.merge_adapter"
_REGISTER = "tokenpal.tools.finetune_voice.register_ollama"


def _make_job() -> TrainingJob:
    return TrainingJob(
        job_id="w-test",
        status=TrainingStatus.QUEUED,
        wiki="adventure-time",
        character="BMO",
        base_model="google/gemma-2-2b-it",
    )


def _mock_profile() -> MagicMock:
    p = MagicMock()
    p.line_count = 100
    p.character = "BMO"
    p.source = "adventure-time.fandom.com"
    p.persona = "A cute robot"
    return p


def test_pipeline_calls_steps_in_order(tmp_path):
    job = _make_job()

    with (
        patch(_WIKI, return_value=_mock_profile()) as m_wiki,
        patch(_PREP, return_value=(tmp_path / "train.jsonl", tmp_path / "val.jsonl")) as m_prep,
        patch(_CHECK_GPU, return_value=True),
        patch(_COUNT, return_value=80),
        patch(_AUTO_TUNE, side_effect=lambda c, n: c),
        patch(_SETUP, return_value=(MagicMock(), MagicMock())),
        patch(_TRAIN, return_value=tmp_path / "adapter"),
        patch(_MERGE),
        patch(_REGISTER, return_value=True),
        patch(_SYS_PROMPT, return_value="prompt"),
    ):
        _run_pipeline(job, tmp_path / "data", tmp_path / "output")

    m_wiki.assert_called_once()
    m_prep.assert_called_once()
    assert job.status == TrainingStatus.REGISTERING
    assert job.model_name == "tokenpal-bmo"
    assert len(job.progress) > 0


def test_pipeline_fails_on_no_lines(tmp_path):
    job = _make_job()

    with (
        patch(_WIKI, return_value=None),
        pytest.raises(ValueError, match="Not enough lines"),
    ):
        _run_pipeline(job, tmp_path / "data", tmp_path / "output")


def test_pipeline_fails_on_no_gpu(tmp_path):
    job = _make_job()

    with (
        patch(_WIKI, return_value=_mock_profile()),
        patch(_PREP, return_value=(tmp_path / "t.jsonl", tmp_path / "v.jsonl")),
        patch(_CHECK_GPU, return_value=False),
        patch(_COUNT, return_value=80),
        patch(_AUTO_TUNE, side_effect=lambda c, n: c),
        pytest.raises(RuntimeError, match="No CUDA GPU"),
    ):
        _run_pipeline(job, tmp_path / "data", tmp_path / "output")


def test_pipeline_updates_progress(tmp_path):
    job = _make_job()

    with (
        patch(_WIKI, return_value=_mock_profile()),
        patch(_PREP, return_value=(tmp_path / "t.jsonl", tmp_path / "v.jsonl")),
        patch(_CHECK_GPU, return_value=True),
        patch(_COUNT, return_value=40),
        patch(_AUTO_TUNE, side_effect=lambda c, n: c),
        patch(_SETUP, return_value=(MagicMock(), MagicMock())),
        patch(_TRAIN, return_value=tmp_path / "adapter"),
        patch(_MERGE),
        patch(_REGISTER, return_value=True),
        patch(_SYS_PROMPT, return_value="prompt"),
    ):
        _run_pipeline(job, tmp_path / "data", tmp_path / "output")

    assert any("Fetching" in msg for msg in job.progress)
    assert any("Training" in msg for msg in job.progress)
    assert any("Merging" in msg for msg in job.progress)
    assert any("Done" in msg for msg in job.progress)
