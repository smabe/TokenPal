# Fine-Tuning Voice Models

## Goal

LoRA fine-tune Gemma 4 on trained voice lines so the model *becomes* the character, not just prompted to act like one.

## Why

Prompt-based voices work but the model still has its own personality underneath. Fine-tuning would make it truly embody the character — Benson wouldn't need to be told to yell, he'd just yell.

## Approach

### Stack
- **Unsloth** + QLoRA — 2-5x faster training, 50-80% less memory than full fine-tune
- **Hugging Face TRL** — Google's official recommended training library for Gemma
- Export to **GGUF** → load in Ollama via Modelfile

### Dataset
Voice lines already exist in `~/.tokenpal/voices/*.json` as structured JSON with:
- `lines` — raw character dialogue (hundreds to thousands per character)
- `persona` — one-sentence voice description
- `mood_prompts` — character-specific mood descriptions

Need a prep pipeline to convert these to chat-format training data.

### Target Hardware
- Mac M-series (MLX path possible but Unsloth is CUDA-first)
- Dell XPS 16 (Intel NPU — likely too slow)
- AMD laptop with RTX 4070 (primary training target)
- AMD desktop with RX 9070 XT (ROCm support TBD)

## Implementation Scope

1. **Dataset prep** — voice lines → chat-format JSONL for training
2. **Training script** — QLoRA via Unsloth, configurable base model + LoRA rank
3. **GGUF export** — convert trained adapter to quantized GGUF
4. **Ollama integration** — auto-create Modelfile, register with Ollama
5. **TokenPal integration** — `/voice finetune <name>` or extend `/voice train`

## Open Questions
- Minimum voice lines needed for meaningful fine-tuning? (likely 200+)
- Training time on RTX 4070 for ~1000 lines?
- Should we fine-tune the full model or just train a LoRA adapter served alongside base?
- ROCm support for RX 9070 XT — does Unsloth work on AMD?
