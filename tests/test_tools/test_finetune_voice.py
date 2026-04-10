"""Tests for the fine-tuning CLI — config, auto-tuning, Modelfile, Ollama registration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from tokenpal.tools.finetune_voice import (
    LoRAConfig,
    auto_tune,
    generate_modelfile,
    register_ollama,
)


def test_lora_config_defaults():
    config = LoRAConfig()
    assert config.lora_rank == 16
    assert config.lora_alpha == 32
    assert config.lora_dropout == 0.05
    assert config.epochs == 3
    assert config.batch_size == 4
    assert config.learning_rate == 2e-4
    assert config.max_seq_length == 512
    assert config.quantization == "q4_k_m"


def test_auto_tune_small_dataset():
    config = LoRAConfig()
    config = auto_tune(config, 100)
    assert config.lora_rank == 8
    assert config.epochs == 5
    assert config.lora_alpha == 16  # 2 * rank


def test_auto_tune_medium_dataset():
    config = LoRAConfig()
    config = auto_tune(config, 400)
    assert config.lora_rank == 8
    assert config.epochs == 4


def test_auto_tune_default_dataset():
    config = LoRAConfig()
    config = auto_tune(config, 1000)
    assert config.lora_rank == 16
    assert config.epochs == 3


def test_auto_tune_large_dataset():
    config = LoRAConfig()
    config = auto_tune(config, 3000)
    assert config.lora_rank == 32
    assert config.epochs == 2
    assert config.lora_alpha == 64


def test_auto_tune_alpha_follows_rank():
    config = LoRAConfig()
    for n in [50, 300, 800, 5000]:
        c = auto_tune(LoRAConfig(), n)
        assert c.lora_alpha == c.lora_rank * 2


def test_generate_modelfile_content():
    content = generate_modelfile(
        Path("/models/test.gguf"),
        "You are a test character.",
    )
    assert "FROM /models/test.gguf" in content
    assert "PARAMETER temperature 0.8" in content
    assert "PARAMETER num_ctx 2048" in content
    assert "You are a test character." in content


def test_generate_modelfile_custom_temperature():
    content = generate_modelfile(
        Path("/models/test.gguf"),
        "Test.",
        temperature=0.5,
    )
    assert "PARAMETER temperature 0.5" in content


@patch("subprocess.run")
def test_register_ollama_success(mock_run: MagicMock):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    result = register_ollama(
        Path("/tmp/test.gguf"), "tokenpal-test", "System prompt.",
    )
    assert result is True
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "ollama" in call_args
    assert "create" in call_args
    assert "tokenpal-test" in call_args


@patch("subprocess.run")
def test_register_ollama_failure(mock_run: MagicMock):
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="model not found",
    )
    result = register_ollama(
        Path("/tmp/test.gguf"), "tokenpal-test", "System prompt.",
    )
    assert result is False


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_register_ollama_no_ollama(mock_run: MagicMock):
    result = register_ollama(
        Path("/tmp/test.gguf"), "tokenpal-test", "System prompt.",
    )
    assert result is False
