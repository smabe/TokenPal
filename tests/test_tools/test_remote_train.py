"""Tests for the remote SSH training orchestrator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tokenpal.config.schema import FinetuneConfig, RemoteTrainConfig
from tokenpal.tools.remote_train import (
    RemoteTrainError,
    _ssh_target,
    remote_finetune,
)
from tokenpal.tools.voice_profile import VoiceProfile


def _make_remote() -> RemoteTrainConfig:
    return RemoteTrainConfig(
        host="gpu-box",
        user="testuser",
        remote_dir="~/training",
        python="python3",
    )


def _make_config() -> FinetuneConfig:
    config = FinetuneConfig()
    config.remote = _make_remote()
    return config


def _make_profile() -> VoiceProfile:
    return VoiceProfile(
        character="Mordecai",
        source="regularshow",
        created="2026-01-01",
        lines=[f"Dude, line {i}." for i in range(100)],
        persona="A blue jay who says dude.",
    )


def test_ssh_target_with_user():
    remote = _make_remote()
    assert _ssh_target(remote) == "testuser@gpu-box"


def test_ssh_target_without_user():
    remote = _make_remote()
    remote.user = ""
    assert _ssh_target(remote) == "gpu-box"


async def test_remote_finetune_no_host():
    config = FinetuneConfig()
    config.remote = RemoteTrainConfig()  # no host
    with pytest.raises(RemoteTrainError, match="No remote host"):
        await remote_finetune(_make_profile(), config)


async def test_remote_finetune_preflight_fail():
    config = _make_config()

    async def _mock_ssh(remote, cmd, progress=None, timeout=3600):
        return (1, "", "Connection refused")

    with patch("tokenpal.tools.remote_train._run_ssh", _mock_ssh):
        with pytest.raises(RemoteTrainError, match="preflight"):
            await remote_finetune(_make_profile(), config)


async def test_remote_finetune_calls_progress():
    """Verify progress callback fires during preflight."""
    config = _make_config()
    progress_msgs: list[str] = []

    call_count = 0

    async def _mock_ssh(remote, cmd, progress=None, timeout=3600):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (0, "GPU OK", "")  # preflight
        if call_count == 2:
            return (0, "", "")  # mkdir
        # Fail on push to stop the pipeline early
        return (1, "", "SCP failed")

    async def _mock_scp(remote, local, remote_path, *, pull=False, timeout=1800):
        return (1, "SCP failed")

    with (
        patch("tokenpal.tools.remote_train._run_ssh", _mock_ssh),
        patch("tokenpal.tools.remote_train._run_scp", _mock_scp),
    ):
        with pytest.raises(RemoteTrainError, match="push"):
            await remote_finetune(
                _make_profile(), config, lambda msg: progress_msgs.append(msg),
            )

    assert any("GPU" in msg for msg in progress_msgs)
