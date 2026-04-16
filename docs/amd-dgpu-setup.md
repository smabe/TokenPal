# AMD dGPU Setup - Windows (RDNA 4 / gfx1201)

Running TokenPal inference on an RX 9060/9070/9070 XT on Windows. If you have a different AMD card (RDNA 2/3), use the Ollama + Vulkan path in the main Windows installer instead - this doc is specifically for RDNA 4.

## Why this exists

Ollama does not run correctly on gfx1201 today:

- **Ollama + Vulkan** loads dense models but produces wrong numerics. The model streams tokens but fails "2+2" and rambles off-topic on normal prompts. Reproducible regardless of chat template.
- **Ollama + ROCm** cannot enumerate the card. Bundled HIP 6 has no gfx1201 kernels; the HIP 7 SDK has them but Ollama's HSA discovery hangs or returns an empty device list. Tracked upstream in ROCm#5812, ollama#9812 / #10430 / #12573 / #13908 / #14686 / #14927. Attempting a HIP 6 to HIP 7 DLL swap (`scripts/amd-hip-swap.ps1`) hits the same wall - kept for RDNA 3 only.

The working path: **lemonade-sdk/llamacpp-rocm**, a nightly build of llama.cpp that ships its own ROCm 7 runtime with native gfx120X kernels. TokenPal's existing HTTP backend is OpenAI-compatible, so no client-side code changes are required - just point `api_url` at llama-server.

When Ollama eventually ships HIP 7 + gfx1201 kernels, the config flip is one line. This doc will be marked obsolete when that lands.

## One-time install

### 1. Download llama-server

Grab the latest `gfx120X` Windows zip from https://github.com/lemonade-sdk/llamacpp-rocm/releases/latest. The filename looks like `llama-b<build>-bin-windows-rocm-gfx120X-x64.zip`.

```powershell
$root = "$env:LOCALAPPDATA\TokenPal"
New-Item -ItemType Directory -Force "$root\llamacpp-rocm" | Out-Null
New-Item -ItemType Directory -Force "$root\models" | Out-Null

# Point $zip at the release asset you downloaded to ~\Downloads
$zip = "$env:USERPROFILE\Downloads\llama-b<build>-bin-windows-rocm-gfx120X-x64.zip"
Expand-Archive -Force $zip -DestinationPath "$root\llamacpp-rocm"

# Verify
& "$root\llamacpp-rocm\llama-server.exe" --version
```

On first launch, Windows SmartScreen will flag `llama-server.exe`. Click **More info -> Run anyway**. No codesigned build is offered upstream.

### 2. Download a GGUF

Pick by VRAM. 16 GB (9070 XT) tested configurations:

| VRAM       | Model                                         | HF file                                                              | On-card size |
| ---------- | --------------------------------------------- | -------------------------------------------------------------------- | ------------ |
| 16 GB      | `gemma-4-26B-A4B-it` MoE (Q3_K_M / IQ3_S)     | `unsloth/gemma-4-26B-A4B-it-GGUF -> gemma-4-26B-A4B-it-UD-IQ3_S.gguf` | ~13.5 GB     |
| 8-16 GB    | `gemma-4-E4B-it` dense (Q4_K_M)                | `unsloth/gemma-4-E4B-it-GGUF -> gemma-4-E4B-it-Q4_K_M.gguf`          | ~5 GB        |
| 6-8 GB     | `gemma-4-E2B-it` dense (Q4_K_M)                | `unsloth/gemma-4-E2B-it-GGUF -> gemma-4-E2B-it-Q4_K_M.gguf`          | ~2.5 GB      |

Q4 variants of the 26B MoE (16.4-17.1 GB) spill on 16 GB cards. Stick to IQ3_S or Q3_K_M for the MoE unless you have >20 GB.

```powershell
$model = "gemma-4-26B-A4B-it-UD-IQ3_S.gguf"  # swap for your pick
$url = "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/$model"
Invoke-WebRequest -Uri $url -OutFile "$root\models\$model"
```

### 3. Launch flags that matter

```powershell
& "$env:LOCALAPPDATA\TokenPal\llamacpp-rocm\llama-server.exe" `
  -m "$env:LOCALAPPDATA\TokenPal\models\gemma-4-26B-A4B-it-UD-IQ3_S.gguf" `
  --host 127.0.0.1 --port 11435 `
  -ngl 99 -c 8192 --no-mmap `
  --jinja --reasoning off
```

Flag notes (all learned the hard way):

- `-ngl 99` - offload all layers to GPU. Watch the startup log for `offloaded N/N layers`. If it reports fewer than the total, your GGUF + context is too big - drop `-c` or pick a smaller quant.
- `-c 8192` - 8k context. MoE gemma-4 defaults to 72k which eats VRAM.
- `--no-mmap` - force full VRAM load instead of memory-mapping; `--no-mmap` gives more predictable VRAM accounting.
- `--jinja` - use the model's built-in chat template. Without this, gemma-4 MoE leaks `<|channel>thought<channel|>` markup into responses.
- `--reasoning off` - disables the reasoning path. gemma-4 MoE is detected as a thinking model by default; without this flag, all output tokens go to `message.reasoning_content` and `message.content` comes back empty. TokenPal reads `content`, sees empty string, logs `Conversation response filtered: ''`, shows a confused quip. Looks like a disconnect; isn't.

### 4. Point TokenPal at it

Edit `config.toml`:

```toml
[llm]
backend = "http"
api_url = "http://localhost:11435/v1"
model_name = "gemma-4-26B-A4B-it-UD-IQ3_S.gguf"
disable_reasoning = true
max_tokens = 80
temperature = 0.7
```

`model_name` must match exactly what `curl http://localhost:11435/v1/models` returns in `data[0].id`. Some builds include `.gguf`, some don't - copy the string verbatim.

Confirm end-to-end:

```powershell
'{"model":"gemma-4-26B-A4B-it-UD-IQ3_S.gguf","messages":[{"role":"user","content":"Say hi in 5 words."}],"max_tokens":40}' | curl.exe -s http://127.0.0.1:11435/v1/chat/completions -H "Content-Type: application/json" --data-binary "@-"
```

Expected: `"content":"Hi, how are you today"` or similar. Empty content or `<|channel|>` markup means one of the server flags isn't applied - re-check step 3.

## Keeping llama-server running

`llama-server` is one-model-per-process and does not auto-start. Options:

- **Manual:** run the launch command in a PowerShell window you leave open.
- **Shortcut:** save the launch command as `start-llamaserver.bat` and drop a shortcut into `shell:startup` (Win+R). Works the same way the existing `start-server.bat` does for the Ollama path.

## Troubleshooting

**Empty bubbles in TokenPal, `Conversation response filtered: ''` in the logs.** See step 3 `--reasoning off` note.

**`<|channel>thought<channel|>` leaking into content.** Add `--jinja`. If still leaking, fall back to `--reasoning-format deepseek --reasoning-budget 0` which routes thoughts to a separate `reasoning_content` field.

**Model generates 512-token rambling essays.** Cap in TokenPal's `config.toml`: `[llm] max_tokens = 80`. Lower temperature to 0.7 if still drifty.

**VRAM spill into shared memory** (Task Manager -> Performance -> GPU -> "Shared GPU memory" > 0). Your GGUF plus KV cache plus compute buffers exceed 16 GB. Drop to a smaller quant (IQ3_S or IQ4_XS), or reduce `-c`.

**`offloaded N/M layers` shows N < M.** Same as above - not all layers fit. Drop quant or context.

**TokenPal still hitting the wrong server.** Verify `api_url` is `http://localhost:11435/v1` (port 11435, not 11434 - Ollama binds 11434 by default, we deliberately use a different port so both can coexist during debugging).

## Performance baseline (2026-04-15)

Build b1-a620695 on RX 9070 XT:

| GGUF                                    | On-card | pp128     | tg128      |
| --------------------------------------- | ------- | --------- | ---------- |
| `gemma-4-E4B-it-Q4_K_M.gguf`            | ~5 GB   | 81 tok/s  | 106 tok/s  |
| `gemma-4-26B-A4B-it-UD-IQ3_S.gguf` MoE  | ~13.5 GB| ~300 tok/s| ~102 tok/s |

Run `llama-bench.exe -m <gguf> -ngl 99 -p 128 -n 128` to reproduce.

## Updating the lemonade build

Nightly builds. When the upstream project tags a newer release:

```powershell
# Remove the old install (keeps GGUFs, only replaces binaries)
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\TokenPal\llamacpp-rocm\*"
# Then repeat step 1 with the new zip
```
