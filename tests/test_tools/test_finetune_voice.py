"""Tests for the fine-tuning CLI — config, auto-tuning, Modelfile, Ollama registration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from tokenpal.tools.finetune_voice import (
    LoRAConfig,
    _resolve_batch_params,
    _should_use_qlora,
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


# ---------------------------------------------------------------------------
# Platform-aware training config (commit 2 of remote-pipeline-windows)
# ---------------------------------------------------------------------------


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=True)
@patch("tokenpal.tools.finetune_voice._is_rocm", return_value=False)
def test_should_use_qlora_windows_skips(_rocm, _windows):
    """Windows+CUDA must NOT trigger QLoRA — bitsandbytes-windows is broken.
    Non-optional regression test per remote-pipeline-windows plan."""
    assert _should_use_qlora() is False


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=False)
@patch("tokenpal.tools.finetune_voice._is_rocm", return_value=True)
def test_should_use_qlora_rocm_skips(_rocm, _windows):
    """ROCm must NOT trigger QLoRA — bitsandbytes on ROCm is unreliable."""
    assert _should_use_qlora() is False


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=False)
@patch("tokenpal.tools.finetune_voice._is_rocm", return_value=False)
def test_should_use_qlora_linux_cuda_uses_qlora(_rocm, _windows):
    """Linux+CUDA is the happy path — QLoRA enabled."""
    assert _should_use_qlora() is True


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=True)
def test_resolve_batch_params_clamps_on_windows(_windows):
    """Windows forces bs=1/accum=4 regardless of config. VRAM-verified at
    bs=1 (7.43 GB), OOMs at bs=2 (9.64 GB) on 8 GB RTX 4070."""
    config = LoRAConfig(batch_size=4, gradient_accumulation_steps=2)
    batch_size, grad_accum = _resolve_batch_params(config)
    assert batch_size == 1
    assert grad_accum == 4


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=False)
def test_resolve_batch_params_passes_through_on_linux(_windows):
    """Linux honors config.batch_size and config.gradient_accumulation_steps."""
    config = LoRAConfig(batch_size=4, gradient_accumulation_steps=2)
    batch_size, grad_accum = _resolve_batch_params(config)
    assert batch_size == 4
    assert grad_accum == 2


@patch("tokenpal.tools.finetune_voice._is_windows", return_value=True)
def test_resolve_batch_params_clamps_even_with_small_config(_windows):
    """On Windows, clamping is absolute — if config is already bs=1, stay at 1
    but reset gradient_accumulation to 4 (which is the tested config)."""
    config = LoRAConfig(batch_size=1, gradient_accumulation_steps=1)
    batch_size, grad_accum = _resolve_batch_params(config)
    assert batch_size == 1
    assert grad_accum == 4
