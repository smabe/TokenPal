# Fine-Tuning Voice Models [SHIPPED]

## Goal

LoRA fine-tune on trained voice lines so the model *becomes* the character, not just prompted to act like one.

## Why

Prompt-based voices work but the model still has its own personality underneath. Fine-tuning makes it truly embody the character — Benson wouldn't need to be told to yell, he'd just yell.

## Stack (as shipped)

- **PEFT + bitsandbytes** (QLoRA) — 4-bit quantized training, ~6GB VRAM for 1-3B models
- **Hugging Face TRL** — `SFTTrainer` with ShareGPT-format data
- **Merge to safetensors** → register with Ollama via `FROM ./merged` Modelfile
- Dropped Unsloth (compatibility issues, PEFT+bitsandbytes is sufficient)
- **Pinned**: `transformers==4.56.1` (4.57.2 has a bug with local model paths)
- **TRL 0.23 fix**: `remove_columns=["conversations"]` when mapping dataset to prevent TRL from re-applying chat template

### Dataset

Voice lines from `~/.tokenpal/voices/*.json` converted to ShareGPT-format JSONL by `dataset_prep.py`:
- **Observation** (75%): screen context → character comment
- **Conversation** (15%): user message → character response
- **Freeform** (10%): unprompted character speech

40+ synthetic context scenarios mirror actual TokenPal sense readings. 90/10 train/val split.

### Target Hardware

- **AMD laptop with RTX 4070** (8GB VRAM) — primary, tested end-to-end
- **AMD desktop with RX 9070 XT** (16GB VRAM) — install.sh detects ROCm, not yet validated
- Mac M-series — orchestrates training via SSH, does not train locally
- Dell XPS 16 (Intel iGPU) — not supported for training, install.sh gives clear error

## What Shipped

1. **Dataset prep** (`tokenpal/tools/dataset_prep.py`) — voice lines → ShareGPT JSONL
2. **Training CLI** (`tokenpal/tools/finetune_voice.py`) — `tokenpal-finetune` with prep/train/merge/export/register/all subcommands
3. **Remote orchestrator** (`tokenpal/tools/remote_train.py`) — SSH/SCP pipeline with:
   - Auto-built wheel bundle (hash-compared, only re-pushed when code changes)
   - `install.sh`: WSL self-relocation, CUDA/ROCm detection, PyTorch index selection, sentinel file
   - Base model download (remote-first, local fallback + SCP push)
   - Training in `tmux` (survives SSH drops), polled every 30s
   - Checkpoint resume (`--resume` auto-detected)
   - `flock` lockfile for concurrent training prevention
   - Merge adapter → safetensors, pull directory, register with Ollama
   - sha256 integrity verification, disk space preflight, actionable error messages
4. **App integration** — `/voice finetune <name>`, `/voice finetune-setup`
5. **Config** — `[finetune]` + `[finetune.remote]` in config.toml
6. **Tests** — 32 tests covering helpers, install.sh content, bundle building, full pipeline mocks

## Open Questions (answered)

- **Minimum voice lines?** — `auto_tune()` adjusts: <200 lines warns (rank=8, epochs=5), 200-500 (rank=8, epochs=4), 500-2000 (rank=16, epochs=3), 2000+ (rank=32, epochs=2)
- **Training time on RTX 4070?** — ~2.5 min for 587 samples (3 epochs) with TinyLlama 1.1B. ~5-10 min with Gemma-2 2B IT for 587 samples (~7.1GB VRAM used)
- **Full model vs LoRA adapter?** — LoRA adapter merged into base model for Ollama (safetensors dir)
- **ROCm for RX 9070 XT?** — install.sh detects ROCm and selects correct PyTorch index, but end-to-end training not yet validated

## See Also

- `docs/remote-training-guide.md` — user-facing setup and usage guide
- `CLAUDE.md` — Fine-Tuning section for architecture details
