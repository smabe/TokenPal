# Remote Training Guide

Fine-tune a voice model on a remote GPU box. Training runs over SSH, survives disconnects, and automatically registers the finished model with your local Ollama.

## Prerequisites

- **Local machine** (macOS): Ollama running, `build` package installed (`pip install build`)
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
# use_wsl = false                     # set true for Windows + WSL hosts
# gpu_backend = "auto"                # auto, cuda, or rocm
```

### Direct WSL SSH (recommended)

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

Your local TokenPal code is packaged into a Python wheel and bundled with an `install.sh` script. This bundle is SCP'd to the remote as a single tarball. A source hash is stored on the remote — subsequent runs skip the push if nothing changed.

### 2. install.sh

The install script runs 6 phases:
1. **WSL relocation** — if on `/mnt/c/`, copies to native Linux filesystem
2. **Python check** — verifies Python 3.12+
3. **GPU detection** — CUDA (via `nvidia-smi`), ROCm (via `rocm-smi`), or Intel NPU (error)
4. **Venv setup** — creates/reuses `~/tokenpal-training/.venv`
5. **PyTorch** — installs with the correct CUDA/ROCm index URL (skips if already working)
6. **TokenPal** — installs the wheel with training dependencies

A sentinel file (`.install-ok`) tracks completion. If install was interrupted, the next run retries automatically.

### 3. Base Model

The base model is downloaded directly on the remote if it has internet access. If that fails, it's downloaded locally and pushed via SCP. The model is cached at `~/tokenpal-training/model/` and reused across runs.

### 4. Training

Training runs inside a `tmux` session on the remote, so it survives SSH disconnects. Progress is polled every 30 seconds and streamed to your terminal/UI.

- Checkpoints are saved per epoch
- If a previous run was interrupted, training resumes from the last checkpoint automatically
- A `flock` lockfile prevents accidental concurrent training

### 5. Merge + Pull

After training, the LoRA adapter is merged back into the base model and saved as safetensors. The merged directory is pulled back to `~/.tokenpal/finetune/models/tokenpal-<name>/`:

- **Linux hosts**: rsync with `--info=progress2` and `--partial` (shows progress, supports resume)
- **WSL hosts**: SCP with `-r` (Windows SSH has no rsync)

### 6. Ollama Registration

A Modelfile is generated pointing to the merged safetensors directory, and the model is registered with Ollama as `tokenpal-<name>`.

## Monitoring

### From the app
Progress messages appear in the speech bubble and terminal output.

### SSH in directly
```bash
ssh you@gpu-box.local
cd ~/tokenpal-training
source .venv/bin/activate

# View training log
cat train.log

# Attach to the live training session
tmux attach -t tokenpal-mordecai

# Check GPU usage
nvidia-smi
```

### Remote directory layout
```
~/tokenpal-training/
  .venv/                  # Python venv with training deps
  .source-hash            # hash of installed training code
  model/                  # base model (cached)
  data/                   # train.jsonl + val.jsonl (per-run)
  output/adapter/         # LoRA checkpoints (per-run)
  output/merged/          # merged safetensors (per-run)
  train.log               # training output
  run_train.sh            # generated training script
  install.sh              # bootstrap script
  tokenpal-*.whl          # installed package
```

## Troubleshooting

### "SCP failed" or "Failed to copy files into WSL"
- Verify SSH key access: `ssh -o BatchMode=yes you@gpu-box echo ok`
- For WSL hosts, ensure the Windows SSH server is running (Settings → Optional Features → OpenSSH Server)

### "PyTorch already installed and CUDA working, skipping" but training fails
- The installed tokenpal wheel may be stale. Delete the sentinel to force reinstall:
  ```bash
  ssh you@gpu-box "wsl -e bash -lc 'rm ~/tokenpal-training/.venv/.install-ok'"
  ```

### SSL errors during PyTorch download (WSL)
- Known WSL2 networking issue with large downloads. install.sh retries 3 times and skips if torch is already installed.
- Manual fix: download torch wheel separately and install from file (see WSL setup lessons in project memory)

### Training fails with transformers/model path errors
- Pin `transformers==4.56.1` — version 4.57.2 has a bug with local model paths. The install script handles this, but if you're running manually on the remote, check your version.

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
