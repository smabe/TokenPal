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

- Python 3.12+
- [Ollama](https://ollama.com) (easiest LLM backend)

### Quick Start

```bash
git clone https://github.com/smabe/TokenPal.git
cd TokenPal
python -m venv .venv
source .venv/bin/activate  # or .\.venv\Scripts\Activate.ps1 on Windows
pip install -e .

# Start Ollama with a small model
ollama pull phi3:mini
ollama serve

# Run TokenPal
python -m tokenpal
```

### Platform-Specific Setup

Detailed setup guides for each target machine:

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
