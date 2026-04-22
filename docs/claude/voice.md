# Voice Training & Fine-Tuning

## Voice Training

- `/voice train`, `/voice regenerate` — structured persona cards with catchphrase priming and cross-franchise guardrails
- ASCII art generation: LLM returns a small JSON classification (skeleton name + 5-color hex palette + eye/mouth glyphs), which is rendered against one of 8 hand-drawn skeleton templates in `tokenpal/ui/ascii_skeletons.py`. Franchise context from `profile.source` is passed to the classifier so it can pick canonical colors. Three frames (idle, idle_alt with blink eye, talking with open mouth) are all rendered from the same skeleton via slot substitution and stored in voice profile JSON as `ascii_idle`, `ascii_idle_alt`, `ascii_talking`. Read `docs/voice-training.md` and `ascii_skeletons.py` before editing either the classifier prompt or the templates
- See `docs/voice-training.md` for persona format, anchor lines, banned names, and architecture

## Fine-Tuning

- Remote LoRA fine-tuning via SSH. Recommended: `google/gemma-2-2b-it` on RTX 4070 (~15 min Windows, ~7 min Linux)
- Two platform paths: native Windows (PowerShell) and Linux/WSL (tmux). ROCm works for RDNA 3; RDNA 4 blocked until ROCm 7.3+
- Pipeline: build wheel -> push bundle -> install -> push base model -> prep data -> train -> merge -> pull -> register (Ollama path: `ollama create`; llamacpp path: GGUF conversion deferred to M4)
- See `docs/remote-training-guide.md` for setup, config, troubleshooting, and developer gotchas
