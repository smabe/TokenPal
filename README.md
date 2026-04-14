# TokenPal

A witty ASCII buddy that lives in your terminal, watches what you're doing, and has opinions about it. Powered by local LLMs via Ollama — run locally or on a remote GPU over your LAN.

```
┌─ buddy ──────────────────────────┐  ┌─ chat log ─┐
│                                  │  │ > hey buddy │
│  ╭──────────────────────────╮    │  │ Oh great,   │
│  │ Oh look, another terminal│    │  │ you again.  │
│  │ window. How original.    │    │  │             │
│  ╰──────────────────────────╯    │  │ > what's up │
│       ▄███▄                      │  │ Not much,   │
│      █ ○ ○ █                     │  │ just judging│
│       ▀███▀                      │  │ your tabs.  │
│                                  │  │             │
│  Type a message or /command...   │  │             │
│  playful | apollyon | BMO | 4s   │  │             │
└──────────────────────────────────┘  └─────────────┘
```

## Quick Start

**Fresh machine?** Clone and run the installer for your platform:

```bash
# macOS
git clone https://github.com/smabe/TokenPal.git && cd TokenPal
bash scripts/install-macos.sh
```

```powershell
# Windows (PowerShell)
git clone https://github.com/smabe/TokenPal.git; cd TokenPal
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

```bash
# Linux
git clone https://github.com/smabe/TokenPal.git && cd TokenPal
bash scripts/install-linux.sh
```

Each installer asks whether you want **Client** (run the buddy), **Server** (serve LLM inference), or **Both**. Recommends a model based on your VRAM (gemma4:26b for 16GB+, gemma4 for 8GB+).

**Already have Python 3.12+?** The lightweight path:

```bash
python3 setup_tokenpal.py   # creates venv, installs deps, checks Ollama
source .venv/bin/activate
tokenpal                    # first run walks you through a quick setup wizard
```

Verify everything: `tokenpal --check` (quick) or `tokenpal --validate` (full preflight)

See [SETUP.md](SETUP.md) for the full decision tree.

## Remote GPU Server

Got a GPU box on your network? One command turns it into an inference server for all your machines. Works with NVIDIA (CUDA) and AMD (Vulkan) GPUs.

**On the GPU box** — run the platform installer with server mode:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Mode Server
```
```bash
# Linux
bash scripts/install-linux.sh --mode server

# macOS
bash scripts/install-macos.sh --mode server
```

Installs Python, Ollama, pulls the right model for your VRAM, configures firewall, sets up auto-start (systemd/launchd/Windows Startup). Prints the URL when done.

**AMD GPU (RX 9070 XT / RDNA 4):** Set Vulkan env vars before first run:
```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
[System.Environment]::SetEnvironmentVariable("GGML_VK_VISIBLE_DEVICES", "0", "User")
# Reopen terminal after setting these
```

**On your client** — switch from inside TokenPal:
```
/server switch gpu-box
```

Or make it permanent in `config.toml`:
```toml
[llm]
api_url = "http://gpu-box:8585/v1"
```

Auto-falls back to local Ollama if the server goes down. Works with [Tailscale](https://tailscale.com) out of the box.

See [docs/server-setup.md](docs/server-setup.md) for details.

## Features

| | |
|---|---|
| **Senses** | App awareness (macOS/Windows), CPU/RAM/battery, idle detection, time of day, weather (Open-Meteo), music (Music.app/Spotify), productivity patterns, git activity (commits, branches, dirty state) |
| **Commentary** | Topic roulette (no 3+ same-topic), change detection ("switched from Chrome"), composite observations, dynamic pacing |
| **Actions** | Timers, system info, open apps — via LLM tool calling |
| **UI** | Textual TUI with split layout — buddy panel + scrollable chat log with timestamps, color-coded status bar, keyboard shortcuts (F1, F2, Ctrl+L) |
| **Voices** | Train character voices from Fandom wiki transcripts, with LLM-generated colored ASCII art per character |
| **Moods** | Custom mood names per character, context-triggered shifts, easter eggs |
| **Conversation** | Multi-turn memory within a session — TokenPal remembers what you said and riffs on it across turns |
| **Memory** | Cross-session app visit history, injected into prompts for continuity |
| **Server** | Remote GPU inference + training over HTTP (NVIDIA CUDA, AMD Vulkan) |
| **Privacy** | No clipboard, no screen capture, silent near banking/health apps, browser titles sanitized |

## Commands

```
/model list              show available models
/model gemma4            switch model
/voice train wiki char   train a voice from a Fandom wiki
/voice switch bmo        hot-swap voice (no restart)
/server status           show server connection
/server switch local     use local Ollama
/server switch hostname  switch to remote server
/zip 90210               set weather location (geocodes, writes config)
/gh                      recent commits (buddy comments in character)
/gh prs                  open pull requests
/gh issues               open issues
/mood                    current mood
/status                  model, senses, actions
```

## Voices

Train a character voice from show transcripts — generates persona, greetings, custom mood names, style hints, and colored ASCII art. Each voice gets its own mood set (BMO gets PLAYFUL/TURBO/BLAH instead of SNARKY/HYPER/BORED) and unique buddy art with idle blink animation:

```bash
/voice train adventuretime BMO     # inside TokenPal
/voice train regularshow Mordecai  # any Fandom wiki works
```

Or write your own persona:
```toml
[brain]
persona_prompt = "You are a grumpy pirate who judges people's computer habits."
```

For deeper character embodiment, [LoRA fine-tune](docs/remote-training-guide.md) a model on the voice's dialogue.

## Configuration

Config is created automatically by the setup script. Edit `config.toml` to customize:

```bash
$EDITOR config.toml   # gitignored, per-machine
```

Config auto-discovered: `~/.tokenpal/config.toml` > project root > cwd.

<details>
<summary>Key settings</summary>

```toml
[llm]
api_url = "http://localhost:11434/v1"  # or remote server
model_name = "gemma4"
disable_reasoning = true               # fast responses

[senses]
# These are on by default:
app_awareness = true
hardware = true
idle = true
time_awareness = true
music = true                           # detect Music.app/Spotify (macOS)
productivity = true                    # work patterns from session data
weather = false                        # opt-in: use /zip or first-run wizard
git = false                            # opt-in: reacts to commits and branch switches

[brain]
comment_cooldown_s = 30.0
active_voice = ""                      # e.g. "bmo"

[conversation]
max_turns = 10                         # turn pairs in history (bump for larger models)
timeout_s = 120                        # seconds of silence before session expires

[actions]
enabled = true

[server]
# host = "0.0.0.0"   # server-side: bind for LAN access
# port = 8585
```

</details>

## Architecture

```
                    ┌─────────┐
User Input ──────▶  │  Brain  │ ──▶ Overlay (ASCII art + speech bubbles)
Senses ──────────▶  │         │
                    └────┬────┘
                    LLM Backend ◀──▶ Actions (tools)
                         │
              ┌──────────┴──────────┐
         Local Ollama        TokenPal Server
                             (remote GPU box)
```

Everything is pluggable via decorators (`@register_sense`, `@register_backend`, `@register_action`). Adding a new sense or action requires zero changes to core code.

<details>
<summary>Project structure</summary>

```
tokenpal/
├── actions/         # LLM-callable tools (timer, system_info, open_app)
├── brain/           # Orchestrator, context builder, personality, memory
├── config/          # TOML schema, loader, weather config helpers
├── llm/             # HTTP backend with auto-fallback (local ↔ remote)
├── senses/          # App awareness, hardware, idle, time, weather, music, productivity, git
├── server/          # FastAPI inference proxy + training API
├── tools/           # Voice training, LoRA fine-tuning, wiki fetch
├── ui/              # Textual TUI overlay (default), console + tkinter fallbacks
├── util/            # Shared utilities
├── commands.py      # Slash command dispatcher
├── cli.py           # --check, --validate, --verbose, --config, --skip-welcome
├── first_run.py     # First-run welcome wizard
└── app.py           # Bootstrap and main loop

scripts/
├── install-macos.sh    # macOS installer (Python, Ollama, deps, client/server/both)
├── install-windows.ps1 # Windows installer (Python, Ollama, deps, client/server/both)
├── install-linux.sh    # Linux installer (Python, Ollama, deps, client/server/both)
├── bootstrap.sh        # Legacy one-line server setup (Linux/macOS)
├── bootstrap.ps1       # Legacy one-line server setup (Windows)
├── install-server.sh   # Legacy server installer (Linux/macOS)
├── install-server.ps1  # Legacy server installer (Windows)
└── start-server.bat    # Start Ollama + server (Windows)

docs/
├── server-setup.md              # Server setup guide
├── remote-training-guide.md     # LoRA fine-tuning guide
├── dynamic-mood-transitions.md  # V2 mood system design (parked)
├── dev-setup-macos.md           # macOS dev environment
├── dev-setup-linux.md           # Linux dev environment
├── dev-setup-windows-*.md       # Windows dev environments (Intel, AMD laptop, AMD desktop)
└── fine-tuning-plan.md          # Fine-tuning architecture notes
```

</details>

## CLI

```
tokenpal                  # run the buddy (first-run wizard on fresh install)
tokenpal --check          # quick: verify Ollama, model, senses, actions
tokenpal --validate       # full: Python, platform deps, git, Ollama, config, permissions
tokenpal --verbose        # debug logs in terminal
tokenpal --config PATH    # specific config file
tokenpal --skip-welcome   # bypass first-run wizard
tokenpal-server           # run the inference server
tokenpal-finetune         # LoRA fine-tuning CLI
```

## Development

```bash
pip install -e ".[macos,dev]"    # macOS
pip install -e ".[windows,dev]"  # Windows
pip install -e ".[server,dev]"   # server extras

pytest                  # tests
ruff check tokenpal/    # lint
tail -f ~/.tokenpal/logs/tokenpal.log  # debug
```

## Privacy

- No clipboard monitoring, no screen content capture
- Goes silent around banking, passwords, health apps
- Browser window titles sanitized (stripped unless music player detected)
- Session memory stores only app names and timestamps, never content
- Conversation history is ephemeral (in-memory only, cleared after ~2 min silence or on sensitive app detection)
- Log files restricted to owner-only (0o600)
- Everything local — no cloud. Optional LAN server for GPU offload
- Weather is the only sense that makes network requests (opt-in, Open-Meteo)
