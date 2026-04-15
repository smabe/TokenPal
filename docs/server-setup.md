# TokenPal Server Setup

Run LLM inference and voice training on a GPU box. Clients on other machines point at the server — no local Ollama or model downloads needed.

## Prerequisites

- Python 3.12+
- GPU with Ollama support (NVIDIA CUDA or AMD Vulkan)
- Network access between client and server machines

Ollama will be installed during setup if not already present.

## Quick Setup

### Windows (tested on RTX 4070 and RX 9070 XT)

```cmd
git clone https://github.com/smabe/TokenPal.git tokenpal-server
cd tokenpal-server
py -3 -m venv .venv
.venv\Scripts\pip.exe install -e ".[server]"
```

Install Ollama if not already installed:
```cmd
winget install Ollama.Ollama
```

Start Ollama (find it in Start Menu, or run directly):
```cmd
"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
```

**AMD GPU (RX 9070 XT / RDNA 4):** Set persistent Vulkan env vars before first run:
```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
[System.Environment]::SetEnvironmentVariable("GGML_VK_VISIBLE_DEVICES", "0", "User")
```
Close and reopen your terminal after setting these. Ollama should show `library=Vulkan` and your GPU name in the serve output.

Pull a model:
```cmd
"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" pull gemma4
```

Add the firewall rule:
```cmd
netsh advfirewall firewall add rule name="TokenPal Server" dir=in action=allow protocol=TCP localport=8585 profile=private
```

Start the server:
```cmd
.venv\Scripts\tokenpal-server.exe --host 0.0.0.0
```

### Linux / macOS

```bash
git clone https://github.com/smabe/TokenPal.git tokenpal-server
cd tokenpal-server
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[server]'

# Install Ollama if needed
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4

tokenpal-server --host 0.0.0.0
```

### Using the platform installers (recommended)

The unified platform installers handle everything — Python, Ollama, model pull, firewall, auto-start:

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Mode Server
```
```bash
# Linux
bash scripts/install-linux.sh --mode server

# macOS
bash scripts/install-macos.sh --mode server
```

They recommend a model based on your VRAM (gemma4:26b for 16GB+, gemma4 for 8GB+).

## Verify

From the server:
```bash
curl http://localhost:8585/api/v1/server/info
```

From a client machine:
```bash
curl http://YOUR-SERVER:8585/api/v1/server/info
```

Expected response:
```json
{"server_version":"0.1.0","api_version":1,"ollama_healthy":true,"ollama_url":"http://localhost:11434","active_training_job":null,"hf_token_set":false}
```

## Client Configuration

On your client machine, edit `config.toml` (project root or `~/.tokenpal/config.toml`):

```toml
[llm]
api_url = "http://YOUR-SERVER:8585/v1"
```

That's it. TokenPal will use the remote server for inference. If the server is unreachable, it falls back to local Ollama automatically (when `mode = "auto"`).

### Per-server model memory

`/model <name>` persists the selection into `[llm.per_server_models]`, keyed
by the active `api_url`. `/server switch` consults that table and restores
the remembered model on the destination host, so machines with different
pulled models (e.g. `gemma4` on a laptop, `gemma4:26b-a4b-it-q8_0` on a
5090) don't clobber each other. The global `model_name` stays as the
fallback for a server you've never used `/model` on.

Optional: `[llm.per_server_max_tokens]` lets you raise the default output
cap on a beefier host (the global default is `60`, tuned for short
observations).

```toml
[llm.per_server_models]
"http://localhost:11434/v1"  = "gemma4"
"http://gpu-box:8585/v1"     = "gemma4:26b-a4b-it-q8_0"

[llm.per_server_max_tokens]
"http://gpu-box:8585/v1" = 256
```

The status bar shows which server is active: `apollyon | gemma4:26b | finn | happy`.

## Voice Training

Voice training runs on the client and uses the server's Ollama for both inference and voice asset generation (persona, greetings, mood prompts).

```
/voice train adventuretime bmo
/voice switch bmo
```

The voice profile is saved locally at `~/.tokenpal/voices/bmo.json`. It contains the persona, example lines, greetings, and mood prompts that shape the prompt sent to the server. The model weights stay on the server — only the prompt engineering happens client-side.

### Fine-tuning (LoRA)

Fine-tuned models are registered directly on the server's Ollama. The fine-tuning pipeline currently runs via SSH (`/voice finetune`). Server-side training via the HTTP API is a future enhancement.

**Important:** Fine-tuned Gemma-2 2B models (`tokenpal-*`) don't support tool calling. Either disable actions (`[actions] enabled = false`) when using a fine-tuned model, or use `gemma4` with voice profiles for the best experience.

### Ollama safetensors registration workaround

Ollama's safetensors converter crashes on Gemma-2 tokenizer format. If `ollama create` fails with a tokenizer panic, convert to GGUF first:

```cmd
pip install gguf
curl -sL -o convert_hf_to_gguf.py https://raw.githubusercontent.com/ggml-org/llama.cpp/b4921/convert_hf_to_gguf.py
python convert_hf_to_gguf.py path\to\merged --outfile tokenpal-model.gguf --outtype f16
```

Then register the GGUF:
```cmd
echo FROM path\to\tokenpal-model.gguf > Modelfile
ollama create tokenpal-model -f Modelfile
```

Note: the `convert_hf_to_gguf.py` script version must match the installed `gguf` pip package version. Tag `b4921` works with `gguf==0.18.0`.

## Server Configuration

The server reads `config.toml` on the server machine. Key settings:

```toml
[server]
host = "0.0.0.0"              # bind to all interfaces for LAN access
port = 8585                    # default port
mode = "auto"                  # auto, remote, or local
auth_backend = "none"          # "none" (v1) or "shared_secret" (v2)
ollama_url = "http://localhost:11434"  # local Ollama instance
```

Default bind is `127.0.0.1` (localhost only). Set `host = "0.0.0.0"` to expose on the LAN.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/server status` | Show connection state and current server |
| `/server switch local` | Use local Ollama and restore its remembered model |
| `/server switch remote` | Use the configured server and restore its remembered model |
| `/server switch HOSTNAME` | Switch to a specific server and restore its remembered model |
| `/model MODEL` | Switch models on the active server (persisted to `[llm.per_server_models]`) |
| `/model list` | Show models available on the server |
| `/voice list` | Show locally available voice profiles |
| `/voice train WIKI CHARACTER` | Train a new voice from a Fandom wiki |
| `/voice switch NAME` | Switch to a voice profile |

## Auto-start

### Windows

The installer creates `start-server.bat`. To auto-start on login, copy it to the Startup folder:
```cmd
copy start-server.bat "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TokenPal-Server.bat"
```

Or create the batch file manually:
```cmd
@echo off
cd /d C:\Users\YourName\tokenpal-server
call .venv\Scripts\activate.bat
tokenpal-server --host 0.0.0.0
pause
```

### Linux

The installer creates a systemd user unit:
```bash
systemctl --user start tokenpal-server
systemctl --user enable tokenpal-server
loginctl enable-linger $USER  # survives logoff
```

## Firewall

**Windows:**
```cmd
netsh advfirewall firewall add rule name="TokenPal Server" dir=in action=allow protocol=TCP localport=8585 profile=private
```

**Linux (ufw):**
```bash
sudo ufw allow 8585/tcp
```

**macOS:** Firewall prompts automatically on first connection.

## HuggingFace Token

For gated models (e.g., Gemma), set HF_TOKEN on the server:

**Linux:** Add `HF_TOKEN=hf_...` to `~/.tokenpal/server.env`
**Windows:** `setx HF_TOKEN "hf_your_token_here"` (persistent across sessions)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Connection refused" from client | Server not running | Start `tokenpal-server` on the server |
| "Connection refused" from client | Firewall blocking port 8585 | Add firewall rule (see above) |
| "Ollama unreachable" in server logs | Ollama not running on server | Start Ollama on the server |
| Ollama not in PATH on Windows | Windows SSH uses cmd.exe | Use full path: `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` |
| Ollama using CPU on AMD GPU | OLLAMA_VULKAN not set | Set `OLLAMA_VULKAN=1` as persistent User env var (see AMD GPU section above) |
| Ollama using system RAM with Vulkan | iGPU detected alongside discrete GPU | Set `GGML_VK_VISIBLE_DEVICES=0` to use only the discrete GPU |
| Model reloads into system RAM after idle | `OLLAMA_KEEP_ALIVE` too short, model evicted from VRAM | Set `OLLAMA_KEEP_ALIVE=24h` (see Model Keep-Alive section) |
| `start-server.bat` says "cannot find the file serve" | `start /B` treats first quoted arg as window title | Re-run the installer (`install-windows.ps1`) or `git pull` |
| `ollama create` from SSH fails with "timed out" | Ollama CLI tries to start new instance | Use PowerShell: `powershell -Command "& 'path\to\ollama.exe' create ..."` |
| `ollama create` panics on safetensors | Tokenizer format incompatibility | Convert to GGUF first (see workaround above) |
| Voice training produces empty persona | Model returns empty content | Fixed: `reasoning_effort=none` added to voice asset generation |
| Fine-tuned model errors on observation | Small model can't handle tool definitions | Use `gemma4` + voice profile instead, or disable actions |
| Status bar shows "fallback" | Server was unreachable at startup | Check server, then `/server switch remote` to reconnect |
| Training OOM | GPU memory insufficient | Unload Ollama models first (automatic in server worker) |
| Training fails with 401 | HF token missing/invalid | Set HF_TOKEN on the server |

## Model Keep-Alive

Ollama unloads models from VRAM after a period of inactivity. The `OLLAMA_KEEP_ALIVE` env var controls this:

| Value | Behavior |
|-------|----------|
| `1m` | Unload after 1 minute (Ollama default) |
| `24h` | Keep loaded for 24 hours — good for dedicated inference boxes |
| `-1` | Never unload |

`start-server.bat` sets this to `24h`. Without it, the model gets evicted during idle periods (e.g., overnight) and must reload on the next request. On AMD Vulkan GPUs, this reload can briefly land in system RAM before migrating to VRAM, causing slow first responses.

If you're running Ollama outside of `start-server.bat`, set the env var yourself:

**Windows (persistent):**
```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "24h", "User")
```

**Linux (systemd):**
Add `Environment="OLLAMA_KEEP_ALIVE=24h"` to your Ollama service unit.

## Security

V1 has no authentication. The server trusts the LAN. This is appropriate for a home network with a single user.

**Do not expose the server to the internet without adding authentication.**

The server binds to `127.0.0.1` by default (localhost only). You must explicitly set `host = "0.0.0.0"` to accept LAN connections.
