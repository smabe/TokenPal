# TokenPal

A sarcastic ASCII buddy that lives in your terminal, watches what you're doing, and won't shut up about it. Powered by local LLMs via Ollama — no cloud, no data leaves your machine.

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

That's it. A chonky ASCII gremlin appears in your terminal and starts roasting your workflow.

### Prerequisites

- **Python 3.12+**
- **Ollama** — install from [ollama.com](https://ollama.com/download), or:
  ```bash
  brew install ollama          # macOS
  winget install Ollama.Ollama # Windows
  ```
- **A model** — the setup script offers to pull one, or:
  ```bash
  ollama pull gemma3:4b
  ```

### Verify Everything Works

```bash
tokenpal --check
```

This tests Ollama connectivity, model availability, senses, and actions in one shot.

## What It Does

TokenPal observes your desktop through modular **senses** and generates short, sarcastic commentary via a local LLM. It never takes action on your behalf — it just watches and judges.

**Senses** (what it can see):
- **App awareness** — foreground app + window title (macOS)
- **Hardware** — CPU, RAM, battery (cross-platform via psutil)
- **Idle detection** — notices when you leave and come back
- **Time awareness** — time of day, session duration

**Actions** (what it can do via LLM tool calling):
- **Timer** — set named countdown timers ("coffee in 5 minutes")
- **System info** — report detailed system stats on demand
- **Open app** — launch apps by name (safety-allowlisted)

**Personality**:
- 6 moods (snarky, impressed, bored, concerned, hyper, sleepy) that shift based on context
- Easter eggs at specific times (3:33 AM, Friday 5 PM, etc.)
- Running gags that track your app usage across sessions
- Voice profiles trained from show transcripts for character-specific commentary
- Goes silent around sensitive apps (banking, passwords, health)

## Configuration

TokenPal works out of the box with defaults. To customize:

```bash
cp config.default.toml config.toml  # config.toml is gitignored
```

Config is found automatically — you can run `tokenpal` from any directory.
Search order: `~/.tokenpal/config.toml` > project root > current directory.

Key settings:

```toml
[llm]
model_name = "gemma3:4b"    # any Ollama model that supports tool calling
temperature = 0.8

[brain]
comment_cooldown_s = 20.0   # seconds between comments
active_voice = ""            # voice profile name (e.g. "bender")

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
```

## Personas & Voices

TokenPal ships with a default sarcastic gremlin persona. You can replace it with a character voice trained from show transcripts, or write your own persona prompt.

### Quick: custom persona prompt

Edit `config.toml` to change what TokenPal sounds like without training a full voice:

```toml
[brain]
persona_prompt = "You are a grumpy pirate who judges people's computer habits. ONE sentence, under 12 words."
```

### Train a voice from transcripts

The voice trainer extracts character dialogue from transcripts (local files or Fandom wikis), then uses Ollama to generate a persona description, startup greetings, and offline quips.

```bash
# From a Fandom wiki (fetches all transcript pages automatically)
python -m tokenpal.tools.train_voice --wiki regularshow "Mordecai"
python -m tokenpal.tools.train_voice --wiki adventuretime "BMO"

# From a local transcript file
python -m tokenpal.tools.train_voice transcript.txt "Character Name"

# From a file of raw quotes (one per line, no character names)
python -m tokenpal.tools.train_voice quotes.txt --lines-only
```

This saves a voice profile to `~/.tokenpal/voices/` and auto-activates it in `config.toml`.

### Manage voices

```bash
# List all saved voice profiles
python -m tokenpal.tools.train_voice --list

# Switch between saved voices (interactive picker)
python -m tokenpal.tools.train_voice --activate

# Or set it directly in config.toml
# [brain]
# active_voice = "mordecai"    # use a trained voice
# active_voice = ""            # back to default TokenPal
```

Restart TokenPal after switching voices.

### Training options

| Flag | Effect |
|------|--------|
| `--preview` | Show extracted lines without saving |
| `--no-persona` | Skip Ollama persona/greetings generation |
| `--min-lines N` | Minimum lines required (default: 10) |
| `--max-pages N` | Max wiki transcript pages to fetch (default: 500) |

## CLI

```
tokenpal              # run the buddy
tokenpal --check      # verify Ollama, model, senses, actions
tokenpal --verbose    # show debug logs in terminal
tokenpal --config PATH # use a specific config file
tokenpal --version    # print version
```

Or use `./run.sh` to skip venv activation.

## Architecture

```
Senses ──▶ Brain ──▶ Overlay
              │
         LLM Backend ◀──▶ Actions
```

- **Senses** poll for context on per-sense intervals
- **Brain** scores interestingness, gates commentary, manages cooldowns
- **LLM Backend** generates quips via Ollama's OpenAI-compatible API
- **Actions** let the LLM call tools (multi-turn execution loop, max 3 rounds)
- **Overlay** renders the ASCII buddy with typing animation and status bar

Everything is pluggable via `@register_sense`, `@register_backend`, `@register_overlay`, and `@register_action` decorators. Adding a new sense or action requires zero changes to core code.

## Project Structure

```
tokenpal/
├── actions/         # LLM-callable tools (timer, system_info, open_app)
├── brain/           # Orchestrator, context builder, personality engine, memory
├── config/          # TOML schema and loader
├── llm/             # LLM backends (HTTP/Ollama with tool-calling support)
├── senses/          # Pluggable sensors (app, hardware, idle, time)
├── tools/           # CLI utilities (voice training, wiki transcripts)
├── ui/              # Console overlay with ASCII art and speech bubbles
├── cli.py           # Argument parsing and --check command
└── app.py           # Bootstrap and main loop
```

## Privacy

- No clipboard monitoring (explicitly rejected)
- No screen content capture in default config
- Sensitive app detection — goes silent around banking, passwords, health apps
- Session memory stores only app names and timestamps, never content
- SQLite db at `~/.tokenpal/memory.db` with restricted permissions
- Everything runs locally — no network calls except to your local Ollama

## Development

```bash
pip install -e ".[macos,dev]"   # macOS
pip install -e ".[windows,dev]" # Windows

# Run tests
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
