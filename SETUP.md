# TokenPal Setup Guide

## Quick Start (fresh machine)

Pick your platform — these install Python, Ollama, and all dependencies:

| Platform | Command |
|----------|---------|
| macOS | `bash scripts/install-macos.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1` |
| Linux | `bash scripts/install-linux.sh` |

Each installer asks whether you want **Client** (run the buddy), **Server** (serve LLM inference), or **Both**.

## Already have Python 3.12+?

```
python3 setup_tokenpal.py          # auto-detect
python3 setup_tokenpal.py --local  # full local + Ollama
python3 setup_tokenpal.py --client # client-only (remote server)
```

## Platform-specific dev guides

Detailed setup with multiple LLM backend options, GPU configuration, and verification:

- [macOS (Apple Silicon)](docs/dev-setup-macos.md)
- [Windows + Intel NPU](docs/dev-setup-windows-intel.md)
- [Windows + NVIDIA GPU](docs/dev-setup-windows-amd.md)
- [Windows + AMD GPU](docs/dev-setup-windows-amd-desktop.md)
- [Linux](docs/dev-setup-linux.md)

## Server setup

- [Remote GPU server guide](docs/server-setup.md)
- [Fine-tuning guide](docs/remote-training-guide.md)

## Verify installation

```
tokenpal --check      # quick: Ollama + model + senses
tokenpal --validate   # full: Python, platform deps, git, Ollama, config, permissions
```
