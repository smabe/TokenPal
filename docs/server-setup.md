# TokenPal Server Setup

Run LLM inference and voice training on a GPU box. Clients on other machines point at the server — no local Ollama or model downloads needed.

## Prerequisites

- Python 3.12+
- Ollama installed and running
- NVIDIA GPU with CUDA drivers (for training)
- Network access between client and server machines

## Quick Setup

### Linux / macOS

```bash
curl -O https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/install-server.sh
bash install-server.sh
```

Or manually:

```bash
python3 -m venv ~/.tokenpal/server-venv
source ~/.tokenpal/server-venv/bin/activate
pip install 'tokenpal[server]'
tokenpal-server --host 0.0.0.0
```

### Windows

```powershell
# Download and run the installer
powershell -ExecutionPolicy Bypass -File install-server.ps1
```

Or manually:

```powershell
py -3 -m venv $env:USERPROFILE\.tokenpal\server-venv
& $env:USERPROFILE\.tokenpal\server-venv\Scripts\activate.ps1
pip install "tokenpal[server]"
tokenpal-server --host 0.0.0.0
```

## Verify

From the server:
```bash
curl http://localhost:8585/api/v1/server/info
```

From a client machine:
```bash
curl http://YOUR-SERVER-HOSTNAME:8585/api/v1/server/info
```

You should see JSON with `server_version`, `ollama_healthy`, etc.

## Client Configuration

On your client machine, edit `~/.tokenpal/config.toml`:

```toml
[llm]
api_url = "http://YOUR-SERVER-HOSTNAME:8585/v1"
```

That's it. TokenPal will use the remote server for inference. If the server is unreachable, it falls back to local Ollama automatically (when `mode = "auto"`).

## Server Configuration

The server reads `~/.tokenpal/config.toml` on the server machine. Key settings:

```toml
[server]
host = "0.0.0.0"              # bind to all interfaces for LAN access
port = 8585                    # default port
mode = "auto"                  # auto, remote, or local
auth_backend = "none"          # "none" (v1) or "shared_secret" (v2)
ollama_url = "http://localhost:11434"  # local Ollama instance
```

Default bind is `127.0.0.1` (localhost only). Set `host = "0.0.0.0"` to expose on the LAN.

## Training a Voice

From the client:
```
/voice train adventuretime bmo
```

This sends a request to the server, which handles wiki fetching, dataset prep, training, merging, and Ollama registration. The model stays on the server.

Check training progress:
```bash
curl http://YOUR-SERVER:8585/api/v1/train/JOB_ID
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/server status` | Show connection state |
| `/server switch local` | Use local Ollama |
| `/server switch remote` | Use the configured server |
| `/server switch HOSTNAME` | Switch to a specific server |

## Firewall

The installer handles firewall rules automatically. If you need to add manually:

**Linux (ufw):**
```bash
sudo ufw allow 8585/tcp
```

**Windows:**
```powershell
New-NetFirewallRule -DisplayName "TokenPal Server" -Direction Inbound -Protocol TCP -LocalPort 8585 -Action Allow -Profile Private
```

**macOS:** Firewall prompts automatically on first connection.

## HuggingFace Token

For gated models (e.g., Gemma), set HF_TOKEN on the server:

**Linux:** Add to `~/.tokenpal/server.env`
**Windows:** `setx HF_TOKEN "hf_your_token_here"`

## Auto-start

**Linux:** The installer creates a systemd user unit:
```bash
systemctl --user start tokenpal-server
systemctl --user status tokenpal-server
```

**Windows:** The installer creates `run-server.bat` and optionally adds a startup shortcut.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Connection refused" from client | Server not running | Start `tokenpal-server` on the server |
| "Connection refused" from client | Firewall blocking | Open port 8585 (see Firewall section) |
| "Ollama unreachable" in server logs | Ollama not running | Run `ollama serve` on the server |
| Training fails with OOM | GPU memory insufficient | Use a smaller base model |
| Training fails with 401 | HF token missing/invalid | Set HF_TOKEN on the server |

## Security

V1 has no authentication. The server trusts the LAN. This is appropriate for a home network with a single user.

**Do not expose the server to the internet without adding authentication.**

The server binds to `127.0.0.1` by default (localhost only). You must explicitly set `host = "0.0.0.0"` to accept LAN connections.
