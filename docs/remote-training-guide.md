# Remote Training Guide

Fine-tune a voice model on a remote GPU box. Training runs over SSH, survives disconnects, and automatically registers the finished model with your local Ollama.

## Prerequisites

- **Local machine** (macOS or Windows): Ollama running, `build` package installed (`pip install build`), `scp` in PATH
- **Remote GPU box**: SSH key access, Python 3.12+, NVIDIA (CUDA) or AMD (ROCm) GPU
- **Voice profile**: Train one first with `/voice train <wiki> "<character>"`

## Quick Start

1. Add to your `config.toml`:
   ```toml
   [finetune.remote]
   host = "gpu-box.local"
   user = "you"
   ```

2. One-time setup:
   ```
   /voice finetune-setup
   ```

3. Fine-tune a voice:
   ```
   /voice finetune mordecai
   ```

That's it. The pipeline handles everything: building the training package, installing deps on the remote, downloading the base model, training, merging, pulling the result, and registering with Ollama.

## Configuration

### `[finetune]` — Training parameters

```toml
[finetune]
base_model = "google/gemma-2-2b-it"  # recommended (ungated, strong for size)
# lora_rank = 16           # auto-tuned by dataset size
# epochs = 3               # auto-tuned by dataset size
# batch_size = 4
# output_dir = "~/.tokenpal/finetune"
```

LoRA rank and epochs are auto-tuned based on voice line count. You generally don't need to touch these.

### Base model options (8GB VRAM)

| Model | Size | Quality | Auth |
|-------|------|---------|------|
| `TinyLlama/TinyLlama-1.1B-Chat-v1.0` | 2GB | Low (testing only) | None |
| **`google/gemma-2-2b-it`** | 5GB | **Strong (recommended)** | **HF token + license** |
| `microsoft/Phi-3.5-mini-instruct` | 7.5GB | Very capable | None |
| `meta-llama/Llama-3.2-3B-Instruct` | 6GB | Good | HF token + license |
| `google/gemma-2-9b-it` | 18GB | Best quality, tight fit | HF token + license |

Most models (Gemma-2, Llama 3.2) are gated — you need an HF token and to accept the license on huggingface.co. Set `HF_TOKEN` on the remote:
```bash
# Add to ~/.bashrc so it's picked up automatically:
ssh you@gpu-box "echo 'export HF_TOKEN=hf_yourtoken' >> ~/.bashrc"
```

### `[finetune.remote]` — GPU box connection

```toml
[finetune.remote]
host = "gpu-box.local"              # SSH hostname or IP
user = "you"                        # SSH user (optional)
# port = 22                           # SSH port (default 22)
# remote_dir = "~/tokenpal-training"  # working directory on remote
# platform = "auto"                   # auto, linux, or windows
# use_wsl = false                     # set true for Windows + WSL hosts (legacy)
# gpu_backend = "auto"                # auto, cuda, or rocm
```

### Windows Native (recommended for Windows GPU hosts)

SSH directly to the Windows host. The pipeline auto-detects Windows and uses PowerShell for installation and cmd.exe for commands. No WSL needed.

**Prerequisites on the Windows GPU host:**
- OpenSSH Server enabled (Settings → Optional Features → OpenSSH Server)
- Python 3.12+ installed from python.org (the `py` launcher is required)
- NVIDIA drivers installed (`nvidia-smi` must be in PATH)

**HF_TOKEN setup** (for gated models like Gemma-2):
```powershell
# Set persistently (needs a new SSH session to take effect):
setx HF_TOKEN hf_your_token_here
# Accept the model license at https://huggingface.co/google/gemma-2-2b-it
```

**Config:**
```toml
[finetune.remote]
host = "gaming-pc.local"
user = "smabe"
platform = "windows"    # or leave as "auto" — detected via SSH probe
```

**What's different from the Linux path:**
- Training runs synchronously in the SSH session (~15 min for Gemma-2 2B). Keep the session open — no tmux on Windows.
- Uses bf16 LoRA with gradient checkpointing + eager attention (7.9 GB peak VRAM on RTX 4070 8GB)
- No concurrent-training lock (no `flock` equivalent)
- CUDA index auto-detected from `nvidia-smi` output (cu126/cu128/cu130)
- Model pull via SCP (no rsync on Windows)

**Migrating from `use_wsl=true`:** Set `platform = "windows"` and `use_wsl = false`. The two flags are incompatible — `platform = "windows"` means native cmd.exe + PowerShell, not WSL bash.

### Direct WSL SSH (recommended for WSL users)

Instead of SSH-ing to Windows and shelling into WSL, run an SSH server **inside WSL** on port 2222. This treats WSL as a native Linux box — no Windows path resolution, no PowerShell quoting, no `/mnt/c/` copies.

**One-time WSL setup:**
```bash
# Inside WSL:
sudo apt install openssh-server
sudo sed -i 's/^#Port 22/Port 2222/' /etc/ssh/sshd_config
sudo service ssh start

# Add CUDA libs to PATH (needed for nvidia-smi inside WSL):
echo 'PATH="/usr/lib/wsl/lib:$PATH"' | sudo tee -a /etc/environment
```

**Windows firewall rule** (run in PowerShell as admin):
```powershell
New-NetFirewallRule -DisplayName "WSL SSH" -Direction Inbound -LocalPort 2222 -Protocol TCP -Action Allow
```

**Config:**
```toml
[finetune.remote]
host = "gaming-pc.local"
user = "smabe"
port = 2222
# use_wsl = false  (default — treats it as native Linux)
```

### Windows + WSL via Windows SSH (legacy)

```toml
[finetune.remote]
host = "gaming-pc.local"
user = "smabe"
use_wsl = true
```

WSL is handled automatically — files are SCP'd to the Windows filesystem, then `install.sh` copies them to the WSL-native ext4 filesystem for performance. This path works but is more fragile due to PowerShell quoting and path translation.

## How It Works

### 1. Wheel Bundle

Your local TokenPal code is packaged into a Python wheel and bundled with both `install.sh` (Linux) and `install.ps1` (Windows). This bundle is SCP'd to the remote as a single tarball. A source hash is stored on the remote — subsequent runs skip the push if nothing changed.

### 2. Install (platform-specific)

**Linux** (`install.sh`) — 6 phases:
1. **WSL relocation** — if on `/mnt/c/`, copies to native Linux filesystem
2. **Python check** — verifies Python 3.12+
3. **GPU detection** — CUDA, ROCm, or Intel NPU. ROCm version detected for correct PyTorch index.
4. **Venv setup** — creates/reuses `~/tokenpal-training/.venv`
5. **PyTorch** — installs with the correct CUDA/ROCm index URL (skips if already working)
6. **TokenPal** — installs the wheel with training dependencies

**Windows** (`install.ps1`) — 6 phases:
1. **Python check** — verifies `py -3.12` launcher
2. **GPU detection** — CUDA only (no ROCm on Windows). Auto-detects CUDA version from `nvidia-smi` for PyTorch index URL.
3. **Venv setup** — creates/reuses `%USERPROFILE%\tokenpal-training\.venv`
4. **TokenPal** — installs the wheel with training extras (before PyTorch, so CUDA torch overwrites any CPU-only transitive deps)
5. **PyTorch (CUDA)** — force-installs from the detected CUDA index. Removes triton (broken Windows binaries, not needed with eager attention).
6. **Verification** — asserts `torch.cuda.is_available()`

Completion is verified on the next run by `_preflight_remote_state` running `python -c "import torch"` on the remote. If install was interrupted or the venv is broken, the next run forces a fresh bundle push + reinstall automatically.

### 3. Base Model

The base model is downloaded directly on the remote if it has internet access. On Linux, a local download + SCP fallback exists if the remote can't reach HuggingFace. On Windows, only remote download is supported — fix HF_TOKEN or network on the remote if it fails.

### 4. Training

**Linux**: Training runs inside a `tmux` session, so it survives SSH disconnects. Progress is polled every 30 seconds.

**Windows**: Training runs synchronously in the SSH session (~15 min for Gemma-2 2B). Keep the session open — SSH-survivable training is a documented non-goal for the Windows MVP.

Both platforms:
- Checkpoints are saved per epoch
- If a previous run was interrupted, training resumes from the last checkpoint automatically
- Linux uses `flock` to prevent concurrent training; Windows skips this (no equivalent)

### 5. Merge + Pull

After training, the LoRA adapter is merged back into the base model and saved as safetensors. The merged directory is pulled back to `~/.tokenpal/finetune/models/tokenpal-<name>/`:

- **Linux hosts**: rsync with `--info=progress2` and `--partial` (shows progress, supports resume)
- **Windows/WSL hosts**: SCP with `-r` (no rsync on Windows). Remote paths use forward slashes to avoid breaking SCP's host:path delimiter

### 6. Ollama Registration

A Modelfile is generated pointing to the merged safetensors directory, and the model is registered with Ollama as `tokenpal-<name>`.

## Monitoring

### From the app
Progress messages appear in the speech bubble and terminal output.

### SSH in directly

**Linux:**
```bash
ssh you@gpu-box.local
cd ~/tokenpal-training
source .venv/bin/activate
cat train.log

# Attach to the live training session
tmux attach -t tokenpal-mordecai

# Check GPU usage
nvidia-smi
```

**Windows:**
```powershell
ssh you@gaming-pc.local
cd %USERPROFILE%\tokenpal-training
.venv\Scripts\activate
type train.log

# Check GPU usage
nvidia-smi
```

### Remote directory layout
```
~/tokenpal-training/          # Linux: ~/tokenpal-training
                              # Windows: %USERPROFILE%\tokenpal-training
  .venv/                      # Python venv with training deps
  .source-hash                # hash of installed training code
  model/                      # base model (cached)
  data/                       # train.jsonl + val.jsonl (per-run)
  output/adapter/             # LoRA checkpoints (per-run)
  output/merged/              # merged safetensors (per-run)
  train.log                   # training output
  run_train.sh                # generated training script (Linux)
  run_train.ps1               # parameterized training runner (Windows)
  install.sh                  # bootstrap script (Linux)
  install.ps1                 # bootstrap script (Windows)
  tokenpal-*.whl              # installed package
```

## Troubleshooting

### "SCP failed" or "Failed to copy files into WSL"
- Verify SSH key access: `ssh -o BatchMode=yes you@gpu-box echo ok`
- For WSL hosts, ensure the Windows SSH server is running (Settings → Optional Features → OpenSSH Server)

### "PyTorch already installed and CUDA working, skipping" but training fails
- The installed tokenpal wheel may be stale. Force a fresh reinstall by nuking the venv — preflight will detect it missing and rebuild on the next run:
  ```bash
  ssh -p 2222 you@gpu-box "rm -rf ~/tokenpal-training/.venv"
  ```
  (Legacy Windows-SSH hosts: wrap in `wsl -e bash -lc '...'`.)

### SSL errors during PyTorch download (WSL)
- Known WSL2 networking issue with large downloads. install.sh retries 3 times and skips if torch is already installed.
- Manual fix: download torch wheel separately and install from file (see WSL setup lessons in project memory)

### Training fails with transformers/model path errors
- Pin `transformers<4.57.2` — version 4.57.2 has a bug with local model paths (`'dict' object has no attribute 'model_type'`). The training extras in `pyproject.toml` enforce this.

### ROCm: GPU detected but model loading hangs
- **RDNA 4 (RX 9070 XT, gfx1201) is not yet viable for training on WSL.** ROCm 7.2 detects the GPU via `librocdxg`, but compute kernels hang indefinitely because gfx1201 lacks native kernel support and the `HSA_OVERRIDE_GFX_VERSION=11.0.0` workaround causes ISA mismatch deadlocks. Wait for ROCm 7.3+ or use native Linux.
- **RDNA 3 (RX 7900 XTX, gfx1100) should work** with the same pipeline — ROCm 6.2 or 7.2 wheels, no GFX override needed.
- QLoRA/bitsandbytes is skipped on ROCm (falls back to bf16 full-precision LoRA) since bitsandbytes ROCm support is unreliable.

### "System role not supported" or chat template errors with Gemma-2
- Gemma-2 models don't support the system role in their chat template. The dataset prep handles this by omitting system messages for Gemma-2.
- If you see this with a custom dataset, ensure your JSONL doesn't include `{"from": "system", ...}` entries.

### Training fails with OutOfMemoryError
- Reduce `batch_size` in `[finetune]` config (try 2 or 1)
- Use a smaller base model (TinyLlama 1.1B fits in 6GB VRAM)

### "Another training job is already running"
- A previous training session is still active. Check with:
  ```bash
  ssh you@gpu-box "wsl -e bash -lc 'tmux list-sessions'"
  ```
- Kill it: `tmux kill-session -t tokenpal-<name>`

### Training interrupted — can I resume?
- Yes. Re-run `/voice finetune <name>` — it detects existing checkpoints and resumes automatically.

## CLI Reference

The `tokenpal-finetune` command can also be used directly on the GPU box:

```bash
tokenpal-finetune prep profile.json -o data/       # voice lines → JSONL
tokenpal-finetune train --data data/ --output out/  # QLoRA training
tokenpal-finetune train --data data/ --output out/ --resume  # resume from checkpoint
tokenpal-finetune merge --adapter out/adapter --output out/merged --base-model ./model
tokenpal-finetune export --adapter out/adapter --output model.gguf  # legacy GGUF path
tokenpal-finetune register --gguf model.gguf --name tokenpal-bmo
tokenpal-finetune all profile.json                  # full local pipeline
```

## Developer Gotchas

Implementation notes for working on `remote_train.py` and `finetune_voice.py`:

### Pinned Dependencies
- `transformers==4.56.1` — 4.57.2 has a bug with local model paths
- `remove_columns=["conversations"]` when mapping dataset to prevent TRL from re-applying chat template
- Gemma-2 does not support system role in chat template

### Windows PowerShell via SSH
- All PowerShell command builders avoid `\"` inside `powershell -Command "..."` — cmd.exe misparses `\"` as string terminators. Use single-quoted strings, concatenation, `[char]10` for newlines
- `install.ps1` requires UTF-8 BOM — without it, PS 5.1 reads as Windows-1252, em dashes become garbled
- `$LASTEXITCODE` is reliable through `Tee-Object` pipelines (tested)
- `run_train.ps1` sets `TORCHDYNAMO_DISABLE=1` (triton import crashes on Windows) and `HF_HUB_OFFLINE=1`
- Training extras installed before CUDA torch — transitive deps pull CPU torch from PyPI otherwise
- Triton uninstalled after torch install (broken Windows binaries)

### SSH/SCP Plumbing
- `RemoteTrainConfig.port` field: `_run_ssh` uses `-p`, `_run_scp` uses `-P`, `_run_rsync` passes `-p` in ssh command
- SCP remote paths must use forward slashes — backslash `C:\` breaks the `host:path` delimiter
- SCP `-r` creates a nested subdir — code renames atomically on success
- Local `scp.exe` must be in PATH on the controller machine

### Model Integrity
- sha256 of safetensors verified after pull — mismatch is a hard error
- Hash computation uses `\n` line separators (not `[Environment]::NewLine` which is `\r\n` on Windows)
- Base model integrity: config.json with `model_type` + nonzero weight shards. Windows variant wrapped in `powershell -Command "..."` (SSH default shell is cmd.exe)

### Ollama Integration
- `ollama create` panics on Gemma-2's `additional_special_tokens` (string format, expects dict). Workaround: convert to GGUF via `convert_hf_to_gguf.py` (b4921 tag matches gguf 0.18.0)
- Fine-tuned 2B models can't handle tool calling — use gemma4 + voice profiles for daily use
- Ollama on Windows: not in PATH from cmd.exe SSH — use full path `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`

### Wheel Bundle
- Auto-built in `remote_finetune()`, hash-compared (`_hash_training_sources()`), only re-pushed when training code changes
- Bundle includes both install.sh and install.ps1 so it works on any platform
- All file I/O uses `encoding="utf-8"` explicitly — Windows defaults to cp1252

### Error Handling
- `RemoteTrainError` includes `hint` with platform-appropriate debug commands
- HF auth errors detected via `_looks_like_hf_auth_error` heuristic on both remote and local paths
- Disk space preflight warns if < 25GB free (Windows: `Get-PSDrive`, Linux: `df -BG`)
- Preflight `_preflight_remote_state` takes `platform` param (no default). Linux: flock/tmux/venv probe. Windows: venv-only

### Legacy WSL Path
- Base64-encoded training scripts (survive SSH→PowerShell→WSL quoting)
- `_resolve_wsl_mount()` for SCP↔WSL bridge
- Direct WSL SSH (port 2222) is now recommended over this path
