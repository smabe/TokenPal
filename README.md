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

**Fresh machine?** One line — no git, no clone, nothing pre-installed required:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/install.sh | bash
```

```powershell
# Windows (PowerShell)
iwr -useb https://raw.githubusercontent.com/smabe/TokenPal/main/install.ps1 | iex
```

The installer asks whether you want **Client** (run the buddy), **Server** (serve LLM inference), or **Both**, then recommends a model based on your detected VRAM:

| VRAM  | Model                     | Size    |
|-------|---------------------------|---------|
| ≥48GB | `llama3.3:70b`            | ~40 GB  |
| ≥32GB | `gemma4:26b-a4b-it-q8_0`  | ~28 GB  |
| ≥16GB | `gemma4:26b` (Q4_K_M)     | ~20 GB  |
| ≥6GB  | `gemma4` (9B)             | ~6 GB   |
| <6GB  | `gemma2:2b`               | ~2 GB   |

Override via `TOKENPAL_MODEL=<model>` env var before running the installer, or pull a different model manually and edit `~/.tokenpal/config.toml`.

To skip the prompt, pass a mode:

```bash
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/install.sh | bash -s -- --mode server
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
| **Senses** | App awareness (macOS/Windows), CPU/RAM/battery, idle detection, time of day, weather (Open-Meteo), music (Music.app/Spotify), productivity patterns, git activity, HN front-page ambient awareness, battery transitions, network state (wifi + VPN), process heat (top CPU hog), typing cadence (WPM bursts + post-burst silence), filesystem pulse (activity bursts in watched dirs) |
| **Commentary** | Topic roulette (no 3+ same-topic), change detection ("switched from Chrome"), composite observations, dynamic pacing |
| **Actions** | Timers, system info, open apps, safe math eval — via LLM tool calling + slash commands |
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
/senses                  list senses with on/off + loaded status
/senses enable <name>    turn a sense on in config.toml (restart to apply)
/senses disable <name>   turn a sense off in config.toml (restart to apply)
/wifi label <name>       label current wifi (SSID hashed, never stored raw)
/watch                   list directories watched by filesystem_pulse
/watch add <path>        add a directory to watch (restart to apply)
/watch remove <path>     stop watching a directory (restart to apply)
/math 2 + 2 * 3          evaluate an arithmetic expression (bypasses the LLM)
/gh                      recent commits (buddy comments in character)
/gh prs                  open pull requests
/gh issues               open issues
/ask <question>          web search (DuckDuckGo + Wikipedia), buddy riffs on result
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

## Enabling opt-in senses

Most senses are on by default. A few are off until you flip them — weather needs a location, git only fires for devs, battery/network/process_heat/typing_cadence/filesystem_pulse are quieter on-transition-only senses best enabled when you actually want them.

From inside TokenPal:

```
/senses                  # list all senses with on/off + loaded status
/senses enable battery
/senses enable network_state
/senses enable process_heat
/senses enable typing_cadence
/senses enable filesystem_pulse
```

Each writes to `config.toml` and reminds you to restart — senses are resolved once at startup, not hot-swapped. Ctrl+C, re-run `tokenpal`, then `/senses` again to verify `(loaded)` next to each.

**Wifi labels** make `network_state` readable. Connect to each network you care about and run:

```
/wifi label home
/wifi label coffee shop
```

TokenPal reads the current SSID, hashes it (sha256[:16]), and upserts the mapping under `[network_state] ssid_labels`. Raw SSID names never hit the config file, the log, or memory.db — only the hash and your chosen label.

**Watch directories** let `filesystem_pulse` react to file activity. Defaults are `~/Downloads`, `~/Desktop`, `~/Documents` — no config required. Add project dirs to react to coding sessions:

```
/watch list              # see current roots (defaults or configured)
/watch add ~/projects/tokenpal
/watch remove ~/Documents
```

Privacy: the sense emits only the leaf directory name in comments (`tokenpal`, never the full path). Filenames are never seen or logged. Heavy dirs like `node_modules`, `.git`, `.venv`, and `__pycache__` are excluded automatically.

**Smoke-testing the new senses:**
- `battery` — unplug your laptop; triggers within ~30s
- `process_heat` — `yes > /dev/null &` in another terminal for ~25s, then `kill %1`
- `network_state` — toggle wifi off/on, or connect to a VPN
- `typing_cadence` — type continuously for 30s+; watch for "User picked up the pace"
- `filesystem_pulse` — save a file 5+ times in a watched dir, or drop a few files into `~/Downloads`

All emit only on transition — steady-state is silent.

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
battery = false                        # opt-in: plug/unplug + low-battery transitions
network_state = false                  # opt-in: online/offline, wifi changes, VPN
process_heat = false                   # opt-in: names the top CPU hog when pinned
typing_cadence = false                 # opt-in: WPM bursts, post-burst silence (counts keypresses only)
filesystem_pulse = false               # opt-in: activity bursts in watched dirs

# [network_state]
# ssid_labels = { "abcd1234abcd1234" = "home", "ffff0000ffff0000" = "coffee shop" }
# Populate via /wifi label <name> from inside the app — raw SSIDs never stored.

# [filesystem_pulse]
# roots = ["/Users/you/projects/tokenpal"]
# Populate via /watch add <path>. Empty = watch ~/Downloads, ~/Desktop, ~/Documents.

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
├── actions/         # LLM-callable tools (timer, system_info, open_app, do_math)
├── brain/           # Orchestrator, context builder, personality, memory
├── config/          # TOML schema, loader, weather config helpers
├── llm/             # HTTP backend with auto-fallback (local ↔ remote)
├── senses/          # App awareness, hardware, idle, time, weather, music, productivity, git, battery, network_state, process_heat, typing_cadence, filesystem_pulse
├── server/          # FastAPI inference proxy + training API
├── tools/           # Voice training, LoRA fine-tuning, wiki fetch
├── ui/              # Textual TUI overlay (default), console + tkinter fallbacks
├── util/            # Shared utilities
├── commands.py      # Slash command dispatcher
├── cli.py           # --check, --validate, --verbose, --config, --skip-welcome
├── first_run.py     # First-run welcome wizard
└── app.py           # Bootstrap and main loop

install.sh              # One-liner bootstrap (macOS/Linux) — curl | bash
install.ps1             # One-liner bootstrap (Windows) — iwr | iex

scripts/
├── install-macos.sh    # macOS installer (Python, Ollama, deps, client/server/both)
├── install-windows.ps1 # Windows installer (Python, Ollama, deps, client/server/both)
├── install-linux.sh    # Linux installer (Python, Ollama, deps, client/server/both)
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
