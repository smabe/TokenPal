# TokenPal

A witty ASCII buddy that lives in your terminal, watches what you're doing, and has opinions about it. Powered by local LLMs via Ollama — run locally or on a remote GPU server over your LAN.

## Quick Start

```bash
# One-command setup (creates venv, installs deps, checks Ollama)
python3 setup_tokenpal.py

# Run it
./run.sh

# Or if you prefer
source .venv/bin/activate
tokenpal
```

That's it. A chonky ASCII buddy appears in your terminal and starts commentating on your workflow.

### Prerequisites

- **Python 3.12+**
- **Ollama** — install from [ollama.com](https://ollama.com/download), or:
  ```bash
  brew install ollama          # macOS
  winget install Ollama.Ollama # Windows
  ```
- **A model** — the setup script offers to pull one, or:
  ```bash
  ollama pull gemma4
  ```

### Verify Everything Works

```bash
tokenpal --check
```

This tests Ollama connectivity, model availability, senses, and actions in one shot.

## What It Does

TokenPal observes your desktop through modular **senses** and generates short, witty commentary via a local LLM. You can also talk to it and ask it to do things.

**Senses** (what it can see):
- **App awareness** — foreground app + window title (macOS)
- **Hardware** — CPU, RAM, battery (cross-platform via psutil)
- **Idle detection** — notices when you leave and come back
- **Time awareness** — time of day, session duration

**Actions** (what it can do via LLM tool calling):
- **Timer** — set named countdown timers ("coffee in 5 minutes")
- **System info** — report detailed system stats on demand
- **Open app** — launch apps by name (safety-allowlisted)

**Text Input** — type messages to the buddy while it's running. It responds conversationally in character, and can use tools when you ask ("open calculator", "set a timer for 5 minutes").

**Slash Commands:**
- `/help` — list commands
- `/model [name|list|pull|browse]` — show, swap, download, or browse models
- `/voice list|switch|off|info|train|finetune` — manage voice profiles live
- `/server status|switch` — manage remote server connection
- `/mood` — show current mood
- `/status` — show model, senses, actions
- `/clear` — clear the speech bubble

**Personality:**
- 6 moods (snarky, impressed, bored, concerned, hyper, sleepy) that shift based on context
- Easter eggs at specific times (3:33 AM, Friday 5 PM, etc.)
- Running gags that track your app usage across sessions
- Voice profiles trained from show transcripts — including character-specific moods and style hints
- Freeform thoughts — trained voices occasionally say things unprompted, in character
- Goes silent around sensitive apps (banking, passwords, health)

**Status Bar** — shows current server, model, voice, mood, and activity:
```
geefourteen | gemma4 | Jake | snarky | spoke 12s ago
```

## Remote Server

Run inference on a GPU box and have any machine on your LAN use it — no local Ollama or model downloads needed. One command sets up everything: Python, Ollama, model, firewall, auto-start.

**On the GPU box (one command):**

Windows (PowerShell):
```powershell
powershell -Command "iwr https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.ps1 -OutFile bootstrap.ps1; .\bootstrap.ps1"
```

Linux / macOS:
```bash
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.sh | bash
```

The script installs Python, Ollama, pulls gemma4, sets up the firewall, and prints the URL to share with clients.

**On your client:**
```toml
# config.toml
[llm]
api_url = "http://gpu-box:8585/v1"
```

That's it. TokenPal proxies all inference through the server. If the server goes down, it auto-falls back to local Ollama. Works with [Tailscale](https://tailscale.com) out of the box — no firewall or port forwarding needed.

Switch between servers on the fly:
```
/server status              # show current connection
/server switch local        # use local Ollama
/server switch gpu-box      # switch to remote server
```

See [docs/server-setup.md](docs/server-setup.md) for manual setup, troubleshooting, and advanced configuration.

## Configuration

TokenPal works out of the box with defaults. To customize:

```bash
cp config.default.toml config.toml  # config.toml is gitignored
```

Config is found automatically — you can run `tokenpal` from any directory.
Search order: `~/.tokenpal/config.toml` > project root > current directory.

Browse and download models from inside TokenPal with `/model browse` and `/model pull <name>`.

Key settings:

```toml
[llm]
model_name = "gemma4"        # any Ollama model
temperature = 0.8
disable_reasoning = true     # skip internal thinking for fast responses

[brain]
comment_cooldown_s = 20.0   # seconds between comments
active_voice = ""            # voice profile name (e.g. "jake")

[senses]
app_awareness = true
hardware = true
idle = true
time_awareness = true

[actions]
enabled = true               # LLM tool calling
timer = true
system_info = true
open_app = true

[paths]
data_dir = "~/.tokenpal"    # logs, memory, voices all live here

[server]
# host = "0.0.0.0"          # bind for LAN access (server-side only)
# port = 8585
```

## Personas & Voices

TokenPal ships with a default witty buddy persona. You can replace it with a character voice trained from show transcripts, or write your own persona prompt.

### Quick: custom persona prompt

Edit `config.toml` to change what TokenPal sounds like without training a full voice:

```toml
[brain]
persona_prompt = "You are a grumpy pirate who judges people's computer habits. 1-2 sentences, keep it short."
```

### Train a voice from transcripts

Train from the CLI or from inside the running buddy:

```bash
# CLI — from a Fandom wiki
python -m tokenpal.tools.train_voice --wiki regularshow "Mordecai"
python -m tokenpal.tools.train_voice --wiki adventuretime "Jake"

# Or live inside TokenPal:
/voice train adventuretime "Finn"
```

Training extracts character dialogue, then generates a persona, startup greetings, offline quips, mood prompts, and style hints via Ollama. Live progress shown in the buddy's speech bubble. Profiles save to `~/.tokenpal/voices/`.

### Manage voices

From inside TokenPal (no restart needed):
```
/voice list              — show saved voices
/voice switch jake       — hot-swap to a trained voice
/voice off               — back to default TokenPal
/voice info              — show current voice
```

Or from the CLI:
```bash
python -m tokenpal.tools.train_voice --list
python -m tokenpal.tools.train_voice --activate
```

### Training options

| Flag | Effect |
|------|--------|
| `--preview` | Show extracted lines without saving |
| `--no-persona` | Skip Ollama persona/greetings generation |
| `--min-lines N` | Minimum lines required (default: 10) |
| `--max-pages N` | Max wiki transcript pages to fetch (default: 500) |

### Fine-tune a voice model (LoRA)

For deeper character embodiment, fine-tune a model on the voice's dialogue lines using a remote GPU:

1. Configure your GPU box in `config.toml`:
   ```toml
   [finetune]
   base_model = "google/gemma-2-2b-it"  # recommended (needs HF token)

   [finetune.remote]
   host = "gpu-box.local"
   user = "you"
   # platform = "auto"  # auto-detects Linux vs Windows
   ```

2. One-time setup: `/voice finetune-setup`
3. Fine-tune: `/voice finetune bmo`

Training runs on the remote GPU via SSH. Linux hosts use tmux (survives disconnects); Windows hosts run synchronously (~15 min). The merged model is automatically downloaded and registered with Ollama. See [docs/remote-training-guide.md](docs/remote-training-guide.md) for setup details and model options.

## CLI

```
tokenpal              # run the buddy
tokenpal --check      # verify Ollama, model, senses, actions
tokenpal --verbose    # show debug logs in terminal
tokenpal --config PATH # use a specific config file
tokenpal --version    # print version
```

Or use `./run.sh` to skip venv activation. On shutdown, the Ollama model is automatically unloaded to free RAM.

## Architecture

```
                    ┌─────────┐
User Input ──────▶  │         │
                    │  Brain  │ ──▶ Overlay
Senses ──────────▶  │         │
                    └────┬────┘
                         │
                    LLM Backend ◀──▶ Actions
                         │
              ┌──────────┴──────────┐
              │                     │
         Local Ollama        TokenPal Server
         (localhost)         (remote GPU box)
                                    │
                               Ollama proxy
                             + Training API
```

- **Senses** poll for context on per-sense intervals
- **Brain** scores interestingness, gates commentary, manages cooldowns and moods
- **LLM Backend** generates quips via Ollama's OpenAI-compatible API (`reasoning_effort: none` for fast responses). Auto-fallback between remote server and local Ollama.
- **Server** (optional) — FastAPI app that proxies inference to Ollama and orchestrates voice training on the GPU box
- **Actions** let the LLM call tools (multi-turn execution loop, max 3 rounds, parallel via asyncio.gather)
- **Overlay** renders the ASCII buddy with typing animation, input line, and status bar
- **User Input** captured in cbreak mode, routed to brain via asyncio.Queue

Everything is pluggable via `@register_sense`, `@register_backend`, `@register_overlay`, and `@register_action` decorators. Adding a new sense or action requires zero changes to core code.

## Project Structure

```
tokenpal/
├── actions/         # LLM-callable tools (timer, system_info, open_app)
├── brain/           # Orchestrator, context builder, personality engine, memory
├── config/          # TOML schema and loader
├── llm/             # LLM backends (HTTP/Ollama with auto-fallback)
├── senses/          # Pluggable sensors (app, hardware, idle, time)
├── server/          # FastAPI server (inference proxy + training API)
├── tools/           # Voice training, LoRA fine-tuning, remote GPU orchestrator
├── ui/              # Console overlay with ASCII art, input, and speech bubbles
├── commands.py      # Slash command dispatcher (/server, /model, /voice, etc.)
├── cli.py           # Argument parsing and --check command
└── app.py           # Bootstrap and main loop
scripts/
├── install-server.sh    # Linux/macOS server installer
└── install-server.ps1   # Windows server installer
```

## Privacy

- No clipboard monitoring (explicitly rejected)
- No screen content capture in default config
- Sensitive app detection — goes silent around banking, passwords, health apps
- Session memory stores only app names and timestamps, never content
- SQLite db at `~/.tokenpal/memory.db` with restricted permissions
- Everything runs locally — no cloud services. Optional LAN server for remote GPU inference

## Development

```bash
pip install -e ".[macos,dev]"   # macOS
pip install -e ".[windows,dev]" # Windows
pip install -e ".[server,dev]"  # server (adds FastAPI + uvicorn)

# Run tests (237 tests)
pytest

# Lint
ruff check tokenpal/

# Logs
tail -f ~/.tokenpal/logs/tokenpal.log
```

Platform-specific setup guides:
- [macOS + Apple Silicon](docs/dev-setup-macos.md)
- [Windows + Intel NPU](docs/dev-setup-windows-intel.md)
- [Windows + AMD + NVIDIA](docs/dev-setup-windows-amd.md)
- [Windows + AMD desktop](docs/dev-setup-windows-amd-desktop.md)
- [Remote GPU training](docs/remote-training-guide.md)
- [Server setup](docs/server-setup.md)
