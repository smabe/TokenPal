# TokenPal

A witty ASCII buddy that lives in your terminal, watches what you're doing, and has opinions about it. Powered by local LLMs via Ollama — run locally or on a remote GPU over your LAN.

```
geefourteen | gemma4 | BMO | playful | spoke 4s ago
┌──────────────────────────────────────────┐
│ Oh look, another terminal window. How    │
│ original. What are we debugging today,   │
│ your life choices?                       │
└──────────────────────────────────────────┘
```

## Quick Start

```bash
python3 setup_tokenpal.py   # creates venv, installs deps, checks Ollama
source .venv/bin/activate
tokenpal                    # first run walks you through a quick setup wizard
```

**Prerequisites:** Python 3.12+, [Ollama](https://ollama.com/download), a model (`ollama pull gemma4`)

Verify everything: `tokenpal --check`

### Client-only install (remote GPU)

If you're using a remote GPU server for inference, skip the Ollama install:

```bash
python3 setup_tokenpal.py --client   # prompts for server URL, skips Ollama
```

## Remote GPU Server

Got a GPU box on your network? One command turns it into an inference server for all your machines.

**On the GPU box:**

```powershell
# Windows (PowerShell)
powershell -Command "iwr https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.ps1 -OutFile bootstrap.ps1; .\bootstrap.ps1"
```
```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.sh | bash
```

Installs Python, Ollama, pulls gemma4, configures firewall, sets up auto-start. Prints the URL when done.

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
| **Senses** | App awareness (macOS), CPU/RAM/battery, idle detection, time of day, weather (Open-Meteo), music (Music.app/Spotify), productivity patterns |
| **Commentary** | Topic roulette (no 3+ same-topic), change detection ("switched from Chrome"), composite observations, dynamic pacing |
| **Actions** | Timers, system info, open apps — via LLM tool calling |
| **Voices** | Train character voices from Fandom wiki transcripts |
| **Moods** | Custom mood names per character, context-triggered shifts, easter eggs |
| **Memory** | Cross-session app visit history, injected into prompts for continuity |
| **Server** | Remote GPU inference + training over HTTP |
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
/mood                    current mood
/status                  model, senses, actions
```

## Voices

Train a character voice from show transcripts — generates persona, greetings, custom mood names, and style hints. Each voice gets its own mood set (BMO gets PLAYFUL/TURBO/BLAH instead of SNARKY/HYPER/BORED):

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

[brain]
comment_cooldown_s = 30.0
active_voice = ""                      # e.g. "bmo"

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
├── senses/          # App awareness, hardware, idle, time, weather, music, productivity
├── server/          # FastAPI inference proxy + training API
├── tools/           # Voice training, LoRA fine-tuning, wiki fetch
├── ui/              # Console overlay with ASCII art and input
├── util/            # Shared utilities
├── commands.py      # Slash command dispatcher
├── cli.py           # --check, --verbose, --config, --skip-welcome
├── first_run.py     # First-run welcome wizard
└── app.py           # Bootstrap and main loop

scripts/
├── bootstrap.sh     # One-line server setup (Linux/macOS)
├── bootstrap.ps1    # One-line server setup (Windows)
├── install-server.sh   # Full server installer
├── install-server.ps1  # Full server installer (Windows)
└── start-server.bat    # Start Ollama + server (Windows)

docs/
├── server-setup.md              # Server setup guide
├── remote-training-guide.md     # LoRA fine-tuning guide
├── dynamic-mood-transitions.md  # V2 mood system design (parked)
├── dev-setup-macos.md           # macOS dev environment
├── dev-setup-windows-*.md       # Windows dev environments
└── fine-tuning-plan.md          # Fine-tuning architecture notes
```

</details>

## CLI

```
tokenpal                  # run the buddy (first-run wizard on fresh install)
tokenpal --check          # verify Ollama, model, senses, actions
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
- Log files restricted to owner-only (0o600)
- Everything local — no cloud. Optional LAN server for GPU offload
- Weather is the only sense that makes network requests (opt-in, Open-Meteo)
