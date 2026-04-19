# llama.cpp-direct Backend for AMD dGPUs

## Goal
Replace the Ollama inference backend with a standalone `llama-server.exe` from lemonade-sdk/llamacpp-rocm when the installer detects an AMD discrete GPU. Ollama's Vulkan backend produces numerically wrong outputs on RDNA 4 (gfx1201) — the model "works" but can't do 2+2. ROCm on Windows is blocked upstream (Ollama bundles HIP 6 without gfx1201 kernels; ROCm 7.1.1 itself has an HSA discovery hang on gfx1201). Only path to correct, fast inference on a 9070 XT today is llama.cpp compiled with native gfx1201 ROCm 7 kernels.

## Background / evidence (2026-04-15 debug session)
- gfx1201 (RX 9070 XT) on Ollama 0.20.6 Windows:
  - Vulkan backend: detects card, loads model, produces **wrong math** (small models fail "2+2", dense 8B variants dump HTML on bare prompts). Confirmed not a template bug — even with a hand-written Modelfile TEMPLATE applying proper Gemma chat format, `gemma4:e4b-it-q8_0` rambled about philosophy when asked basic arithmetic.
  - ROCm backend: bundled `amdhip64_6.dll` has no gfx1201 kernels. `HSA_OVERRIDE_GFX_VERSION=11.0.0` doesn't help because HIP never enumerates the 9070 XT at the bootstrap stage. Known upstream: ollama#13920, ollama#14927, ROCm#5812.
  - `gemma4:26b` (MoE, ~18 GB, hybrid CPU/GPU split) is the only model that produces coherent output — likely because MoE routing sidesteps the broken dense-matmul kernels, and extra capacity absorbs small numerical errors.
- lemonade-sdk/llamacpp-rocm ships nightly Windows binaries of llama.cpp with ROCm 7 and native gfx120X kernels. Same stack tlee933/llama.cpp-rdna4-gfx1201 reports 98 tok/s on 9070 XT, competitive with RTX 4070 Ti.
- llama-server exposes `/v1/chat/completions` — API-compatible with what `tokenpal/server/app.py` already proxies, so the inference proxy is byte-transparent.

## Non-goals
- Replacing Ollama on NVIDIA boxes (CUDA path is rock-solid; no reason to churn)
- Replacing Ollama on AMD iGPU-only boxes (Vulkan is fine for smaller models on RDNA 2/3 iGPUs)
- Replacing Ollama on Apple Silicon (Metal/MLX works correctly)
- Supporting model pull/browse via TokenPal slash commands for the llama-server path (drop these for AMD dGPU installs; users manage GGUFs manually or via the installer)
- Hot model swap (llama-server is one-model-per-process; accept restart-to-switch for this path)

## Scope

### 1. Detect AMD dGPU in installers, offer llama.cpp-direct path
Windows installer (`scripts/install-windows.ps1`) already has `$amdGpu` detection via the video adapter name regex. Extend it to distinguish iGPU (gfx103x, integrated graphics) vs dGPU (Radeon RX *, discrete). On dGPU detection, prompt the user:

```
AMD discrete GPU detected (RX 9070 XT).
Ollama's Vulkan backend has known correctness issues on RDNA 4.
Install llama-server (llama.cpp with native ROCm 7 kernels) instead? [Y/n]
```

Default Y. If accepted:
- Download latest `gfx120X` zip from github.com/lemonade-sdk/llamacpp-rocm/releases/latest
- Extract to `%LOCALAPPDATA%\TokenPal\llamacpp-rocm\`
- Skip the Ollama install step entirely
- Write a `start-llamaserver.bat` in the repo root (replacing `start-server.bat` for this box) with the correct `llama-server.exe -m <gguf> --port 11434 -ngl 99` invocation
- Download a gfx1201-appropriate GGUF directly (see model tier logic below)

Linux installer (`scripts/install-linux.sh`) — same decision tree for AMD dGPU with gfx1100/gfx1101/gfx1102/gfx1151/gfx120X. `rocminfo` detects the arch; switch on it.

macOS — skip entirely, Metal is fine.

### 2. Model management on the llama.cpp-direct path
llama-server is one-model-per-process. Design decisions:
- **Single default model per box.** Installer picks it based on VRAM (same tiers as the Ollama path, but using true dGPU VRAM not system RAM — the Windows installer fix from commit 830cbeb already handles this).
- **GGUFs live under `%LOCALAPPDATA%\TokenPal\models\`**. Installer downloads the chosen model directly from HuggingFace (huggingface.co/<repo>/resolve/main/<file>.gguf) — no Ollama registry involvement.
- **`/model list|pull|browse` slash commands disabled** when `backend = "llamacpp"`. Show a message pointing at a doc about manual GGUF management. Don't try to re-implement Ollama's registry shim.
- **Model switching: restart.** Add a `/model switch <name>` helper that stops llama-server, updates `start-llamaserver.bat`, relaunches. Slow but honest.

### 3. Server code changes (`tokenpal/server/`)
Thin layer, most of the work is config-driven. The proxy in `app.py:38-76` already accepts an `ollama_url` kwarg — rename it to `inference_url` (deprecating alias kept for one release), and point at `http://localhost:11434` regardless of backend. llama-server binds the same port so **the proxy itself needs zero changes** for inference.

What does need changes:
- **`worker.py:72-85`** — the "unload Ollama models to free VRAM before training" trick uses Ollama's `/api/ps` + `/api/generate keep_alive:0`. llama-server doesn't have this API. Replace with: send SIGTERM to the llama-server process, wait for VRAM to clear, run training, relaunch llama-server after merge step.
- **`worker.py:107-114`** — `register_ollama(merged_dir, model_name, system_prompt)` registers the fine-tuned model via `ollama create`. For llama.cpp-direct: convert the merged HuggingFace dir to GGUF via `llama.cpp/convert_hf_to_gguf.py` (bundled in the lemonade release), save to `%LOCALAPPDATA%\TokenPal\models\<name>.gguf`, update `start-llamaserver.bat` to point at it, relaunch.
- **`/model pull` slash command** — `tokenpal/ui/slash_commands.py` (or wherever it lives) gates on backend. Ollama path: existing behavior. llama.cpp path: prints "llama-server manages models manually, see docs/amd-dgpu-setup.md".

### 4. Config schema
Add `[llm] backend = "ollama" | "llamacpp"` (default `"ollama"`). Everything downstream keys off this:
- Slash command availability
- Training-pipeline VRAM-unload method
- Error-message wording ("Is Ollama running?" vs "Is llama-server running?")

### 5. Documentation
- New `docs/amd-dgpu-setup.md` — explains why this path exists, how to swap GGUFs manually, how to update the lemonade binary when a new nightly drops, known-good models by VRAM tier.
- Update `docs/dev-setup-windows-amd-desktop.md` to reflect the new recommended path (currently recommends Vulkan, which this session proved broken).
- Update `CLAUDE.md` architecture section — note the dual-backend setup, point at the new docs.

## Open questions
- **Does lemonade-sdk ship a stable API?** Releases are nightly. Need to pin to a specific release tag in the installer so a surprise upstream breakage doesn't brick fresh installs. Check their release cadence + whether they tag stable milestones.
- **Can we auto-update?** A `tokenpal --update-llamacpp` command that redownloads the latest gfx120X zip and relaunches. Nice-to-have, not P0.
- **Windows Defender / SmartScreen.** Downloaded `llama-server.exe` will trip SmartScreen on first launch. Document the "More info → Run anyway" dance, or ship the binary ourselves and codesign. Codesigning is a whole project of its own; for now just document.
- **Fine-tuning pipeline on AMD dGPU.** Currently the remote training guide targets Linux/WSL. If the user's 9070 XT box is also their training target, the pipeline needs end-to-end llama.cpp-awareness (GGUF conversion after merge). If training always happens remotely, this is a non-issue for the installer.
- **What about AMD dGPU + Ollama users who don't want to switch?** Default Y with an escape hatch. If they say N, continue installing Ollama as today, document the Vulkan correctness caveat in the install output.

## Milestones
1. **M1 — Prove the path end-to-end on the user's 9070 XT box.** Download lemonade zip by hand, run `llama-server.exe` against a gemma GGUF, confirm it answers "what is 2+2" with "4". Benchmark tokens/sec against the current gemma4:26b Ollama+Vulkan setup. Decision point: if perf isn't >2x, reconsider whether the refactor is worth it.
2. **M2 — Dual-backend server code.** Add `[llm] backend` config, gate `worker.py` VRAM-unload + `register_ollama` behind it, disable Ollama-specific slash commands when llamacpp. Ships independently of the installer work.
3. **M3 — Installer integration.** AMD dGPU detection, lemonade download/extract, `start-llamaserver.bat` generation, direct GGUF pull. Windows first, Linux second.
4. **M4 — Training pipeline llama.cpp awareness.** GGUF conversion after merge, auto-registration in llama-server config. Only needed if local fine-tuning on AMD is a real use case.
5. **M5 — Docs.** `docs/amd-dgpu-setup.md`, update of dev-setup and CLAUDE.md.

## What this does NOT solve
- Ollama itself on AMD Windows stays broken for RDNA 4. We're sidestepping, not fixing.
- When Ollama eventually ships HIP 7 with gfx1201 support, the llama.cpp-direct path becomes optional again. Keep the backend toggle so users can switch back trivially.
- NVIDIA users see zero benefit and zero change. That's intentional.
