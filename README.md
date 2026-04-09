# TokenPal

A cross-platform AI desktop buddy that watches what you're doing and comments on it. Powered by local LLMs running on your NPU, GPU, or CPU — no cloud required.

TokenPal is a small ASCII character that floats on your screen as a transparent overlay. It passively observes your activity through modular "senses" (foreground app, clipboard, hardware stats, screen content, music, and more), then generates short, sarcastic commentary using a local language model.

## Status

Early development. Building core abstractions and first working prototype on macOS.

## Features

**Passive Commentator** — TokenPal watches and reacts but never takes action. Think of it as a sarcastic roommate glancing at your screen.

**Modular Senses** — Each thing TokenPal can perceive is a pluggable module with platform-specific implementations:

| Sense | What it detects | AI required |
|---|---|---|
| App awareness | Foreground app + window title | No |
| Time awareness | Time of day, session duration | No |
| Idle detection | Time since last input | No |
| Hardware monitoring | CPU, RAM, GPU, NPU, thermals, fans, per-process breakdown | No |
| Clipboard | Text copied to clipboard | No |
| Music | Currently playing track | No |
| Network / Disk | I/O rates, free space | No |
| Screen reading | Visual content on screen | Yes (vision model) |
| OCR | Text on screen | Yes |
| Voice | Speech via push-to-talk | Yes (Whisper) |
| Web search | On-demand answers | Yes (LLM + search) |

**Swappable LLM Backends** — Use whatever runs best on your hardware:

| Backend | Best for |
|---|---|
| HTTP (Ollama / LM Studio) | Any machine, zero setup |
| MLX | Apple Silicon Macs |
| llama.cpp (CUDA) | NVIDIA GPUs |
| llama.cpp (ROCm/Vulkan) | AMD GPUs |
| ONNX Runtime (OpenVINO) | Intel NPUs |

**Cross-Platform** — Runs on macOS and Windows with platform-aware implementations for each sense, overlay, and inference backend.

## Supported Hardware

| Machine | Inference target | Notes |
|---|---|---|
| Mac (Apple Silicon) | GPU via Metal/MLX | Primary dev machine. ANE for small background models. |
| Windows + Intel Core Ultra | NPU via OpenVINO | Copilot+ PC. Phi Silica + Windows AI APIs. |
| Windows + NVIDIA GPU | GPU via CUDA | RTX 4070+. Most mature inference ecosystem. |
| Windows + AMD GPU | GPU via ROCm or Vulkan | RX 9070 XT. 16 GB VRAM fits 13B+ models. |

## Architecture

TokenPal is built around three swappable abstractions:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Senses    │────▶│    Brain    │────▶│   Overlay   │
│ (pluggable) │     │(orchestrator)│     │ (pluggable) │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────▼──────┐
                    │ LLM Backend │
                    │ (pluggable) │
                    └─────────────┘
```

- **Senses** poll for context (app, time, hardware, screen, etc.)
- **Brain** assembles context, decides when something is interesting, and prompts the LLM
- **LLM Backend** generates commentary in character
- **Overlay** renders the ASCII buddy and speech bubble

Every component uses abstract base classes with `@register` decorators for plugin discovery. Adding a new sense or backend requires zero changes to core code.

Configuration is driven by TOML files — `config.default.toml` ships with sane defaults, `config.toml` (gitignored) holds machine-specific overrides.

## Getting Started

### Prerequisites

- **Python 3.12+** — [python.org](https://www.python.org/downloads/) or `brew install python` on macOS
- **Ollama** — local LLM runner that TokenPal talks to via HTTP

### 1. Install Ollama

Ollama runs language models locally and exposes an OpenAI-compatible API that TokenPal connects to.

**macOS:**
```bash
brew install ollama
brew services start ollama   # runs in background
```

**Windows:**
```powershell
winget install Ollama.Ollama
# Ollama runs as a system service after install
```

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then pull a model (Phi-3 mini is small and fast — ~2.2 GB):
```bash
ollama pull phi3:mini
```

Verify it's working:
```bash
ollama run phi3:mini "Say hello in 5 words"
```

### 2. Install TokenPal

```bash
git clone https://github.com/smabe/TokenPal.git
cd TokenPal
python3 -m venv .venv
```

Activate the virtual environment:
```bash
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Install with platform extras:
```bash
# macOS (Apple Silicon)
pip install -e ".[macos,dev]"

# Windows
pip install -e ".[windows,dev]"

# Windows with NVIDIA GPU monitoring
pip install -e ".[windows,nvidia,dev]"
```

**macOS note:** If you get `No module named '_tkinter'`, install tkinter for your Python version:
```bash
brew install python-tk@3.12   # match your Python version
```

### 3. Run TokenPal

Make sure Ollama is running, then:
```bash
python -m tokenpal
```

A small ASCII buddy will appear in the corner of your screen. It polls your system every few seconds and generates sarcastic commentary when something interesting happens.

To stop: `Ctrl+C` or close the window.

### 4. Customize (optional)

Copy the defaults and edit to taste:
```bash
cp config.default.toml config.toml
```

`config.toml` is gitignored — it's your machine-specific settings. See [Configuration](#configuration) below.

### Alternative LLM Backends

TokenPal works with any OpenAI-compatible local API, not just Ollama:

| Backend | Install | Config |
|---|---|---|
| [Ollama](https://ollama.com) | `brew install ollama` | `api_url = "http://localhost:11434/v1"` (default) |
| [LM Studio](https://lmstudio.ai) | Download from site | `api_url = "http://localhost:1234/v1"` |
| [Foundry Local](https://learn.microsoft.com/en-us/windows/ai/overview) | Windows only | `api_url = "http://localhost:5272/v1"` |

Just change `api_url` in your `config.toml` under `[llm]`.

### Platform-Specific Dev Setup

Detailed guides for setting up each target machine for development:

- [macOS + Apple Silicon](docs/dev-setup-macos.md)
- [Windows + Intel Core Ultra (NPU)](docs/dev-setup-windows-intel.md)
- [Windows + AMD + NVIDIA GPU](docs/dev-setup-windows-amd.md)
- [Windows + AMD CPU + AMD GPU](docs/dev-setup-windows-amd-desktop.md)

## Configuration

```toml
# config.toml (machine-specific, gitignored)

[senses]
app_awareness = true
hardware = true
time_awareness = true
clipboard = true
screen_capture = false    # enable for AI-powered screen reading

[llm]
backend = "http"          # "http" | "mlx" | "llamacpp" | "onnx"
api_url = "http://localhost:11434/v1"

[brain]
poll_interval_s = 2.0
comment_cooldown_s = 15.0
persona_prompt = """You are TokenPal, a tiny ASCII creature on a desktop.
You just comment on what you see, like a sarcastic roommate.
Keep comments under 15 words. Be funny, not mean."""

[ui]
overlay = "auto"
position = "bottom_right"
```

## Project Structure

```
tokenpal/
├── config/          # TOML config schema and loader
├── brain/           # Orchestrator, context builder, personality engine
├── senses/          # Pluggable sense modules (each with platform impls)
├── llm/             # Swappable LLM backends
├── ui/              # Overlay renderers (tkinter, macOS NSWindow)
└── util/            # Platform detection, logging
```
