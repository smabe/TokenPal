"""LoRA fine-tuning CLI for voice models.

Standalone script that runs on a GPU machine. Converts a voice profile
to training data, fine-tunes via QLoRA (Unsloth + TRL), exports GGUF,
and registers the model with Ollama.

Usage:
    tokenpal-finetune all ~/.tokenpal/voices/mordecai.json
    tokenpal-finetune prep ~/.tokenpal/voices/mordecai.json -o ./data/
    tokenpal-finetune train --data ./data/ --output ./lora-out/
    tokenpal-finetune export --adapter ./lora-out/ --output ./model.gguf
    tokenpal-finetune register --gguf ./model.gguf --name tokenpal-mordecai
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LoRA config with auto-tuning
# ---------------------------------------------------------------------------


@dataclass
class LoRAConfig:
    """QLoRA hyperparameters — auto-tuned by dataset size."""

    base_model: str = "google/gemma-2-9b"
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple[str, ...] = (
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    )
    epochs: int = 3
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.03
    max_seq_length: int = 512
    quantization: str = "q4_k_m"


def auto_tune(config: LoRAConfig, num_lines: int) -> LoRAConfig:
    """Adjust LoRA hyperparameters based on dataset size."""
    if num_lines < 200:
        log.warning(
            "Only %d lines — fine-tuning may overfit. "
            "Consider collecting more voice data.",
            num_lines,
        )
        config.lora_rank = 8
        config.epochs = 5
    elif num_lines <= 500:
        config.lora_rank = 8
        config.epochs = 4
    elif num_lines <= 2000:
        config.lora_rank = 16
        config.epochs = 3
    else:
        config.lora_rank = 32
        config.epochs = 2

    # Alpha = 2 * rank is a common heuristic
    config.lora_alpha = config.lora_rank * 2
    return config


# ---------------------------------------------------------------------------
# Training pipeline steps
# ---------------------------------------------------------------------------


def _check_gpu() -> bool:
    """Verify CUDA is available."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _count_lines(path: Path) -> int:
    """Count lines in a JSONL file (UTF-8)."""
    return sum(1 for _ in path.open(encoding="utf-8"))


def _is_rocm() -> bool:
    """Detect if PyTorch is using ROCm (HIP) backend."""
    try:
        import torch
        return hasattr(torch.version, "hip") and torch.version.hip is not None
    except ImportError:
        return False


def _is_windows() -> bool:
    """Detect if we're running on native Windows (not WSL).

    When use_wsl=true in remote config, the training script runs inside
    WSL bash, so platform.system() returns 'Linux' and this returns False.
    Only returns True when training code is executing in a native Windows
    Python process — which is the case we need to dodge bitsandbytes on.
    """
    import platform
    return platform.system() == "Windows"


def _should_use_qlora() -> bool:
    """Use QLoRA (4-bit via bitsandbytes) only when the environment supports it.

    ROCm: bitsandbytes ROCm is unreliable on RDNA 3/4. Skip.
    Windows: `bitsandbytes-windows` is community-maintained and frequently
             broken. Skip — use bf16 LoRA with gradient checkpointing instead
             (VRAM-verified: 7.43 GB on RTX 4070 8GB card for Gemma-2 2B).
    Linux CUDA: the happy path.
    """
    return not (_is_rocm() or _is_windows())


def _resolve_batch_params(config: LoRAConfig) -> tuple[int, int]:
    """Return (batch_size, gradient_accumulation_steps) respecting platform limits.

    On Windows, forces batch_size=1 and gradient_accumulation_steps=4
    regardless of config, because bf16 LoRA + gradient checkpointing only
    fits at bs=1 on an 8 GB RTX 4070 (measured: 7.43 GB at bs=1, 9.64 GB
    at bs=2 which OOMs). auto_tune()'s output for these two fields is
    overridden on Windows; lora_rank and epochs are still auto-tuned normally.

    On any other platform (Linux CUDA QLoRA, Linux ROCm bf16), honors the
    config values as-is.
    """
    if _is_windows():
        return 1, 4
    return config.batch_size, config.gradient_accumulation_steps


def setup_model(
    config: LoRAConfig,
) -> tuple[Any, Any]:
    """Load base model with LoRA (QLoRA on CUDA-Linux, full-precision bf16 elsewhere).

    Gate matrix:
      Linux + CUDA → QLoRA (4-bit, ~1.3 GB weights)
      Linux + ROCm → bf16 LoRA (bitsandbytes unreliable on ROCm)
      Windows + CUDA → bf16 LoRA (bitsandbytes-windows unreliable)
      Windows + ROCm → bf16 LoRA (both reasons apply)

    On bf16 paths, uses `attn_implementation="eager"` and gradient
    checkpointing (enabled via SFTConfig in train()) to fit Gemma-2 2B
    in 8 GB VRAM. Eager is unconditional on the bf16 path because
    Gemma-2 is the committed target model.

    Returns (model, tokenizer).
    """
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    use_quantization = _should_use_qlora()

    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    if use_quantization:
        from peft import prepare_model_for_kbit_training
        from transformers import BitsAndBytesConfig

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype="float16",
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model,
            quantization_config=bnb_config,
            device_map="auto",
        )
        model = prepare_model_for_kbit_training(model)
        log.info("Using QLoRA (4-bit quantization via bitsandbytes)")
    else:
        import torch

        # VRAM-critical config — if you change ANY of the three knobs below
        # (bf16, eager attention, gradient checkpointing via SFTConfig) you
        # must re-measure VRAM on the 8 GB card. Measured peak: 7.43 GB at
        # bs=1/seq=512, 1.16 GB headroom on Gemma-2 2B.
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            attn_implementation="eager",  # Gemma-2 recommendation
        )
        reason = "ROCm backend" if _is_rocm() else "Windows host"
        log.info(
            "Using full-precision bf16 LoRA with eager attention (%s detected)",
            reason,
        )

    lora_config = LoraConfig(
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=list(config.target_modules),
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    return model, tokenizer


def _sharegpt_to_chatml(conversation: list[dict[str, str]]) -> str:
    """Convert a ShareGPT conversation to ChatML text format."""
    parts: list[str] = []
    for turn in conversation:
        role = turn["from"]
        if role == "system":
            parts.append(f"<|im_start|>system\n{turn['value']}<|im_end|>")
        elif role == "human":
            parts.append(f"<|im_start|>user\n{turn['value']}<|im_end|>")
        elif role == "gpt":
            parts.append(f"<|im_start|>assistant\n{turn['value']}<|im_end|>")
    return "\n".join(parts)


def train(
    model: Any,
    tokenizer: Any,
    train_path: Path,
    val_path: Path,
    config: LoRAConfig,
    output_dir: Path,
    resume_from_checkpoint: str | None = None,
) -> Path:
    """Run QLoRA fine-tuning via TRL SFTTrainer.

    Returns the path to the saved adapter directory.
    """
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    dataset = load_dataset(
        "json",
        data_files={
            "train": str(train_path),
            "validation": str(val_path),
        },
    )

    def _format(examples: dict[str, list[Any]]) -> dict[str, list[str]]:
        texts = [
            _sharegpt_to_chatml(convo)
            for convo in examples["conversations"]
        ]
        return {"text": texts}

    dataset = dataset.map(_format, batched=True, remove_columns=["conversations"])

    adapter_dir = output_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)

    # Platform-aware batch params. On Windows, bf16 LoRA + grad checkpointing
    # fits at bs=1 only (9.64 GB at bs=2 → OOM on 8 GB card). On Linux+CUDA
    # QLoRA, config values pass through unchanged.
    batch_size, grad_accum = _resolve_batch_params(config)

    # Gradient checkpointing only needed on the bf16 path. QLoRA's 4-bit
    # weights are small enough that grad_checkpointing adds compute overhead
    # with no memory payoff. Enable via SFTConfig so TRL handles the
    # PEFT wiring (enable_input_require_grads etc).
    use_grad_checkpoint = not _should_use_qlora()

    training_args = SFTConfig(
        output_dir=str(adapter_dir),
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=grad_accum,
        num_train_epochs=config.epochs,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        bf16=True,
        gradient_checkpointing=use_grad_checkpoint,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1,
        save_strategy="epoch",
        eval_strategy="epoch",
        report_to="none",
        dataset_text_field="text",
        max_length=config.max_seq_length,
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        args=training_args,
    )

    log.info("Starting training: %d epochs, rank %d", config.epochs, config.lora_rank)
    trainer.train(resume_from_checkpoint=resume_from_checkpoint)
    trainer.save_model(str(adapter_dir))

    return adapter_dir


def export_gguf(
    adapter_dir: Path,
    output_path: Path,
    base_model: str,
    quantization: str = "q4_k_m",
) -> Path:
    """Merge LoRA adapter into base model and export quantized GGUF.

    Uses llama.cpp's convert script. Requires llama-cpp-python or
    the llama.cpp repo on the remote machine.
    Returns the path to the GGUF file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_dir = output_path.parent / "merged"

    merge_adapter(adapter_dir, base_model, merged_dir)

    log.info("Converting to GGUF (quantization: %s)...", quantization)
    result = subprocess.run(
        [
            sys.executable, "-m", "llama_cpp.convert",
            str(merged_dir),
            "--outfile", str(output_path),
            "--outtype", quantization,
        ],
        capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        # Fallback: try llama.cpp convert_hf_to_gguf.py if available
        result = subprocess.run(
            [
                sys.executable, "convert_hf_to_gguf.py",
                str(merged_dir),
                "--outfile", str(output_path),
                "--outtype", quantization,
            ],
            capture_output=True, text=True, timeout=1800,
        )
    if result.returncode != 0:
        log.error("GGUF conversion failed: %s", result.stderr[-500:])
        msg = (
            "GGUF conversion failed. Install llama-cpp-python or "
            "clone llama.cpp for convert_hf_to_gguf.py"
        )
        raise RuntimeError(msg)

    log.info("Exported GGUF: %s (%.1f MB)", output_path, output_path.stat().st_size / 1e6)
    return output_path


def merge_adapter(
    adapter_dir: Path,
    base_model: str,
    output_dir: Path,
) -> Path:
    """Merge LoRA adapter into base model and save as safetensors.

    Returns the path to the merged model directory.
    """
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Merging LoRA adapter into base model...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype="auto", device_map="cpu",
    )
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    model = model.merge_and_unload()

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    log.info("Merged model saved: %s", output_dir)
    return output_dir


def generate_modelfile(
    model_path: Path,
    system_prompt: str,
    temperature: float = 0.8,
) -> str:
    """Generate an Ollama Modelfile for a custom model.

    model_path can be a GGUF file or a safetensors directory.
    """
    return (
        f"FROM {model_path}\n"
        f"PARAMETER temperature {temperature}\n"
        f"PARAMETER num_ctx 2048\n"
        f'SYSTEM """{system_prompt}"""\n'
    )


def register_ollama(
    model_path: Path,
    model_name: str,
    system_prompt: str,
) -> bool:
    """Register a model with Ollama.

    model_path can be a GGUF file or a safetensors directory.
    Creates a Modelfile and runs ``ollama create``.
    Returns True on success.
    """
    modelfile_content = generate_modelfile(model_path, system_prompt)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".Modelfile", delete=False,
    ) as f:
        f.write(modelfile_content)
        modelfile_path = f.name

    try:
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", modelfile_path],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            log.error("ollama create failed: %s", result.stderr)
            return False

        log.info("Registered model: %s", model_name)
        return True
    except FileNotFoundError:
        log.error("ollama not found — is it installed?")
        return False
    finally:
        Path(modelfile_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_prep(args: argparse.Namespace) -> None:
    """Prepare training data from a voice profile."""
    from tokenpal.tools.dataset_prep import prepare_dataset

    profile_path = Path(args.profile)
    output_dir = Path(args.output) if args.output else profile_path.parent / "finetune-data"

    train_path, val_path = prepare_dataset(profile_path, output_dir)

    # Count lines for reporting
    train_count = _count_lines(train_path)
    val_count = _count_lines(val_path)

    print(f"Train: {train_path} ({train_count} samples)")
    print(f"Val:   {val_path} ({val_count} samples)")


def _cmd_train(args: argparse.Namespace) -> None:
    """Run QLoRA training."""
    if not _check_gpu():
        print("ERROR: No CUDA GPU detected. Training requires a CUDA GPU.")
        sys.exit(1)

    data_dir = Path(args.data)
    output_dir = Path(args.output)
    train_path = data_dir / "train.jsonl"
    val_path = data_dir / "val.jsonl"

    if not train_path.exists():
        print(f"ERROR: {train_path} not found. Run 'prep' first.")
        sys.exit(1)

    # Count lines to auto-tune config
    num_lines = _count_lines(train_path)
    config = LoRAConfig(base_model=args.base_model)
    config = auto_tune(config, num_lines)

    if args.rank:
        config.lora_rank = args.rank
        config.lora_alpha = args.rank * 2
    if args.epochs:
        config.epochs = args.epochs

    print(f"Config: rank={config.lora_rank}, epochs={config.epochs}, "
          f"lr={config.learning_rate}, batch={config.batch_size}")

    model, tokenizer = setup_model(config)
    resume_from = None
    if args.resume:
        # Find latest checkpoint
        adapter_dir = output_dir / "adapter"
        checkpoints = sorted(adapter_dir.glob("checkpoint-*")) if adapter_dir.exists() else []
        if checkpoints:
            resume_from = str(checkpoints[-1])
            print(f"Resuming from: {resume_from}")
        else:
            print("No checkpoints found, starting fresh.")

    adapter_dir = train(
        model, tokenizer, train_path, val_path, config, output_dir,
        resume_from_checkpoint=resume_from,
    )
    print(f"Adapter saved: {adapter_dir}")


def _cmd_merge(args: argparse.Namespace) -> None:
    """Merge LoRA adapter into base model as safetensors."""
    adapter_dir = Path(args.adapter)
    output_dir = Path(args.output)

    merged_dir = merge_adapter(adapter_dir, args.base_model, output_dir)
    print(f"Merged model saved: {merged_dir}")


def _cmd_export(args: argparse.Namespace) -> None:
    """Export trained adapter to GGUF."""
    adapter_dir = Path(args.adapter)
    output_path = Path(args.output)

    gguf_path = export_gguf(
        adapter_dir, output_path, args.base_model, args.quantization,
    )
    print(f"GGUF exported: {gguf_path}")


def _cmd_register(args: argparse.Namespace) -> None:
    """Register a GGUF model with Ollama."""
    gguf_path = Path(args.gguf).resolve()
    if not gguf_path.exists():
        print(f"ERROR: {gguf_path} not found.")
        sys.exit(1)

    system_prompt = args.system_prompt or ""
    if register_ollama(gguf_path, args.name, system_prompt):
        print(f"Model registered: {args.name}")
    else:
        print("ERROR: Failed to register model with Ollama.")
        sys.exit(1)


def _cmd_all(args: argparse.Namespace) -> None:
    """Full pipeline: prep → train → export → register."""
    from tokenpal.tools.dataset_prep import prepare_dataset
    from tokenpal.tools.voice_profile import load_profile

    profile_path = Path(args.profile)
    voices_dir = profile_path.parent
    slug = profile_path.stem
    profile = load_profile(slug, voices_dir)

    if not _check_gpu():
        print("ERROR: No CUDA GPU detected. Training requires a CUDA GPU.")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else (
        Path.home() / ".tokenpal" / "finetune" / slug
    )
    model_name = f"tokenpal-{slug}"

    # Step 1: Prep data
    print(f"[1/4] Preparing training data ({profile.line_count} lines)...")
    data_dir = output_dir / "data"
    train_path, val_path = prepare_dataset(profile, data_dir)
    num_train = _count_lines(train_path)
    print(f"  Train: {num_train} samples, Val: {_count_lines(val_path)} samples")

    # Step 2: Train
    config = LoRAConfig(base_model=args.base_model or "google/gemma-2-9b")
    config = auto_tune(config, num_train)
    print(f"[2/4] Training (rank={config.lora_rank}, epochs={config.epochs})...")
    model, tokenizer = setup_model(config)
    adapter_dir = train(model, tokenizer, train_path, val_path, config, output_dir)
    print(f"  Adapter: {adapter_dir}")

    # Step 3: Merge adapter into base model
    merged_dir = output_dir / "merged"
    print("[3/4] Merging adapter into base model...")
    merge_adapter(adapter_dir, config.base_model, merged_dir)
    print(f"  Merged: {merged_dir}")

    # Step 4: Register with Ollama
    from tokenpal.tools.dataset_prep import build_system_prompt
    system_prompt = build_system_prompt(profile)
    print(f"[4/4] Registering {model_name} with Ollama...")
    if register_ollama(merged_dir, model_name, system_prompt):
        print(f"\nDone! Model '{model_name}' is ready.")
        print(f"  Test it:  ollama run {model_name}")
        print(f"  In app:   /model {model_name}")
    else:
        print("\nWARNING: Model merged but Ollama registration failed.")
        print(f"  Manual:   ollama create {model_name} -f <Modelfile>")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="tokenpal-finetune",
        description="LoRA fine-tune voice models for TokenPal",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # prep
    p_prep = sub.add_parser("prep", help="prepare training data from voice profile")
    p_prep.add_argument("profile", help="path to voice profile JSON")
    p_prep.add_argument("-o", "--output", help="output directory")

    # train
    p_train = sub.add_parser("train", help="run QLoRA training")
    p_train.add_argument("--data", required=True, help="data directory with JSONL")
    p_train.add_argument("--output", required=True, help="output directory for adapter")
    p_train.add_argument("--base-model", default="google/gemma-2-9b")
    p_train.add_argument("--rank", type=int, help="override LoRA rank")
    p_train.add_argument("--epochs", type=int, help="override epoch count")
    p_train.add_argument("--resume", action="store_true", help="resume from latest checkpoint")

    # merge
    p_merge = sub.add_parser("merge", help="merge adapter into base model (safetensors)")
    p_merge.add_argument("--adapter", required=True, help="adapter directory")
    p_merge.add_argument("--output", required=True, help="output directory for merged model")
    p_merge.add_argument("--base-model", default="google/gemma-2-9b")

    # export (legacy — use merge instead for Ollama safetensors support)
    p_export = sub.add_parser("export", help="export adapter to GGUF")
    p_export.add_argument("--adapter", required=True, help="adapter directory")
    p_export.add_argument("--output", required=True, help="GGUF output path")
    p_export.add_argument("--base-model", default="google/gemma-2-9b")
    p_export.add_argument(
        "--quantization", default="q4_k_m", help="GGUF quantization method",
    )

    # register
    p_reg = sub.add_parser("register", help="register GGUF with Ollama")
    p_reg.add_argument("--gguf", required=True, help="path to GGUF file")
    p_reg.add_argument("--name", required=True, help="Ollama model name")
    p_reg.add_argument("--system-prompt", default="", help="system prompt for Modelfile")

    # all
    p_all = sub.add_parser("all", help="full pipeline: prep → train → export → register")
    p_all.add_argument("profile", help="path to voice profile JSON")
    p_all.add_argument("-o", "--output", help="output directory")
    p_all.add_argument("--base-model", default="")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    commands = {
        "prep": _cmd_prep,
        "train": _cmd_train,
        "merge": _cmd_merge,
        "export": _cmd_export,
        "register": _cmd_register,
        "all": _cmd_all,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
