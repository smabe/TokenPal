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


def setup_model(
    config: LoRAConfig,
) -> tuple[Any, Any]:
    """Load base model with QLoRA via PEFT + bitsandbytes.

    Returns (model, tokenizer).
    """
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="float16",
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        quantization_config=bnb_config,
        device_map="auto",
    )
    model = prepare_model_for_kbit_training(model)

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
) -> Path:
    """Run QLoRA fine-tuning via TRL SFTTrainer.

    Returns the path to the saved adapter directory.
    """
    from datasets import load_dataset
    from transformers import TrainingArguments
    from trl import SFTTrainer

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

    dataset = dataset.map(_format, batched=True)

    adapter_dir = output_dir / "adapter"
    adapter_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(adapter_dir),
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        num_train_epochs=config.epochs,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        fp16=True,
        logging_steps=1,
        save_strategy="epoch",
        eval_strategy="epoch",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        args=training_args,
        dataset_text_field="text",
        max_seq_length=config.max_seq_length,
    )

    log.info("Starting training: %d epochs, rank %d", config.epochs, config.lora_rank)
    trainer.train()
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
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_dir = output_path.parent / "merged"

    log.info("Merging LoRA adapter into base model...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype="auto", device_map="cpu",
    )
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    model = model.merge_and_unload()

    model.save_pretrained(str(merged_dir))
    tokenizer.save_pretrained(str(merged_dir))

    log.info("Converting to GGUF (quantization: %s)...", quantization)
    import subprocess
    result = subprocess.run(
        [
            "python3", "-m", "llama_cpp.convert",
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
                "python3", "convert_hf_to_gguf.py",
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


def generate_modelfile(
    gguf_path: Path,
    system_prompt: str,
    temperature: float = 0.8,
) -> str:
    """Generate an Ollama Modelfile for a custom GGUF."""
    return (
        f"FROM {gguf_path}\n"
        f"PARAMETER temperature {temperature}\n"
        f"PARAMETER num_ctx 2048\n"
        f'SYSTEM """{system_prompt}"""\n'
    )


def register_ollama(
    gguf_path: Path,
    model_name: str,
    system_prompt: str,
) -> bool:
    """Register a GGUF model with Ollama.

    Creates a Modelfile and runs ``ollama create``.
    Returns True on success.
    """
    modelfile_content = generate_modelfile(gguf_path, system_prompt)

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
    try:
        from tokenpal.tools.dataset_prep import prepare_dataset
    except ModuleNotFoundError:
        from dataset_prep import prepare_dataset  # type: ignore[no-redef]

    profile_path = Path(args.profile)
    output_dir = Path(args.output) if args.output else profile_path.parent / "finetune-data"

    train_path, val_path = prepare_dataset(profile_path, output_dir)

    # Count lines for reporting
    train_count = sum(1 for _ in train_path.open())
    val_count = sum(1 for _ in val_path.open())

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
    num_lines = sum(1 for _ in train_path.open())
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
    adapter_dir = train(model, tokenizer, train_path, val_path, config, output_dir)
    print(f"Adapter saved: {adapter_dir}")


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
    try:
        from tokenpal.tools.dataset_prep import prepare_dataset
    except ModuleNotFoundError:
        from dataset_prep import prepare_dataset  # type: ignore[no-redef]
    try:
        from tokenpal.tools.voice_profile import load_profile
    except ModuleNotFoundError:
        from voice_profile import load_profile  # type: ignore[no-redef]

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
    num_train = sum(1 for _ in train_path.open())
    print(f"  Train: {num_train} samples, Val: {sum(1 for _ in val_path.open())} samples")

    # Step 2: Train
    config = LoRAConfig(base_model=args.base_model or "google/gemma-2-9b")
    config = auto_tune(config, num_train)
    print(f"[2/4] Training (rank={config.lora_rank}, epochs={config.epochs})...")
    model, tokenizer = setup_model(config)
    adapter_dir = train(model, tokenizer, train_path, val_path, config, output_dir)
    print(f"  Adapter: {adapter_dir}")

    # Step 3: Export GGUF
    gguf_path = output_dir / f"{slug}.gguf"
    print(f"[3/4] Exporting GGUF ({config.quantization})...")
    gguf_path = export_gguf(adapter_dir, gguf_path, config.base_model, config.quantization)
    size_gb = gguf_path.stat().st_size / 1e9
    print(f"  GGUF: {gguf_path} ({size_gb:.1f} GB)")

    # Step 4: Register with Ollama
    try:
        from tokenpal.tools.dataset_prep import build_system_prompt
    except ModuleNotFoundError:
        from dataset_prep import build_system_prompt  # type: ignore[no-redef]
    system_prompt = build_system_prompt(profile)
    print(f"[4/4] Registering {model_name} with Ollama...")
    if register_ollama(gguf_path, model_name, system_prompt):
        print(f"\nDone! Model '{model_name}' is ready.")
        print(f"  Test it:  ollama run {model_name}")
        print(f"  In app:   /model {model_name}")
    else:
        print("\nWARNING: GGUF exported but Ollama registration failed.")
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

    # export
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
        "export": _cmd_export,
        "register": _cmd_register,
        "all": _cmd_all,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
