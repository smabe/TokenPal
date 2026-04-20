# Dev Environment Setup — Windows + Ryzen 9800X3D + Radeon RX 9070 XT (Desktop)

Target: AMD Ryzen 7 9800X3D, Radeon RX 9070 XT (16 GB VRAM, RDNA 4). Desktop.

**Primary inference target: RX 9070 XT via `llama-server` from lemonade-sdk/llamacpp-rocm.** The 16 GB VRAM makes this your most capable inference machine.

> **Status (April 2026):** Ollama does not work correctly on RDNA 4. Vulkan backend produces wrong numerics on dense gemma-4 models; ROCm backend fails to enumerate the card. Use the llama.cpp-direct setup in [docs/amd-dgpu-setup.md](amd-dgpu-setup.md) instead - ships its own ROCm 7 runtime with native gfx120X kernels, ~106 tok/s on gemma-4 E4B dense and ~102 tok/s on gemma-4 26B MoE (IQ3_S). The rest of this doc still applies for Windows/Python/training-side setup; only the inference-backend section is superseded.

---

## 1. Prerequisites

### Windows version
- Windows 11 23H2 or later
- Check: `winver` in Run dialog

### Python
- Python 3.12+ (x86_64)
- Install from https://www.python.org/downloads/
- **Check "Add to PATH"**
- Verify: `python --version`

### Git
- `winget install Git.Git`

### Visual Studio Build Tools
- `winget install Microsoft.VisualStudio.2022.BuildTools`
- Select **"Desktop development with C++"** workload

---

## 2. AMD GPU Driver + ROCm Setup (Critical)

### Install AMD Adrenalin driver
1. Download latest from: https://www.amd.com/en/support
2. Select: Radeon RX 9070 XT → Windows 11
3. Install and reboot
4. Verify in Device Manager: "AMD Radeon RX 9070 XT" under Display adapters

### AMD HIP SDK (ROCm on Windows)
ROCm on Windows provides the HIP runtime needed for GPU-accelerated inference.

```powershell
# Download HIP SDK from: https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html
# Install HIP SDK 6.3+ (required for RDNA 4 / gfx1200 support)
# Make sure gfx120x target is included

# After install, verify:
hipcc --version
hipinfo
# Should show: gfx1200 (or gfx1201) for the 9070 XT
```

**Important:** ROCm/HIP version must be 6.3 or later for RDNA 4 (gfx120x) support. Earlier versions do not include the necessary libraries.

### Verify GPU is visible
```powershell
# If hipinfo is available:
hipinfo
# Should list your 9070 XT with gfx1200 architecture

# Or check via Python later with PyTorch
```

---

## 3. Python Environment

### Create project venv
```powershell
cd C:\Users\<you>\projects\TokenPal
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Core dependencies
```powershell
pip install --upgrade pip

# Core
pip install psutil pywin32 mss pyperclip pynput

# Hardware monitoring
pip install wmi
# Note: pynvml is NVIDIA-only, NOT applicable here. See AMD monitoring section below.

# Config
pip install tomli-w

# Windows-specific senses
pip install winrt-Windows.Media.Control  # Music detection (SMTC)

# Screen reading (Tier 2)
pip install pytesseract

# Web search (on-demand)
pip install duckduckgo-search

# Voice
pip install pyttsx3

# Dev tools
pip install pytest pytest-asyncio ruff mypy
```

---

## 4. LLM Backend

### Option A: llama.cpp-direct via lemonade-sdk (recommended)

This is the only correct+fast path on RDNA 4 as of April 2026. The Windows installer (`scripts/install-windows.ps1 -Mode Server`) handles everything automatically: lemonade zip download, GGUF pull, `start-llamaserver.bat` generation, and `config.toml` setup.

For manual setup or troubleshooting, see [docs/amd-dgpu-setup.md](amd-dgpu-setup.md).

### Option B: Ollama with Vulkan (NOT recommended on RDNA 4)

Ollama's Vulkan backend loads models but produces wrong numerics on dense gemma-4 variants (fails "2+2", rambles on normal prompts). The ROCm backend cannot enumerate gfx1201 at all. **Do not use Ollama on a 9070 XT for inference.** If Ollama ships HIP 7 + gfx1201 kernels in the future, flip `[llm] inference_engine = "ollama"` in config.toml to switch back.

```powershell
# Only use if you explicitly want to test Ollama's Vulkan path:
winget install Ollama.Ollama
[System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
[System.Environment]::SetEnvironmentVariable("GGML_VK_VISIBLE_DEVICES", "0", "User")
# WARNING: dense model outputs will be numerically wrong on gfx1201.
```

### Option C: PyTorch with ROCm
```powershell
# PyTorch ROCm builds for Windows
pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm6.3

# Verify
python -c "import torch; print(f'ROCm: {torch.cuda.is_available()}, Device: {torch.cuda.get_device_name(0)}')"
# Note: PyTorch uses CUDA API names even for ROCm (torch.cuda.* works for both)
# Should show the 9070 XT

# For vision models (Florence-2, Moondream)
pip install transformers accelerate pillow
```

---

## 5. Model Downloads

### GGUF models for llama.cpp / Ollama
```powershell
pip install huggingface-hub

# With 16 GB VRAM, you can go bigger than the laptop:
# - Phi-3-mini Q4 (~2.3 GB) — lightweight, fast
# - Llama-3-8B Q4 (~4.7 GB) — great quality
# - Llama-3-13B Q4 (~7.4 GB) — fits comfortably in 16 GB
# - Qwen2.5-14B Q4 (~8.2 GB) — strong multilingual
# - Llama-3-8B Q8 (~8.5 GB) — higher quality quantization since you have the VRAM
# - Mixtral-8x7B Q4 (~24 GB) — WON'T fit, don't try

huggingface-cli download TheBloke/Llama-2-13B-chat-GGUF llama-2-13b-chat.Q4_K_M.gguf --local-dir models/
```

### Vision models
```powershell
# Same as AMD laptop setup — Florence-2 or Moondream via transformers + ROCm PyTorch
```

---

## 6. AMD GPU Monitoring (No pynvml!)

NVIDIA's `pynvml` does not work with AMD GPUs. AMD GPU monitoring options:

### Option A: WMI queries
```python
import wmi

w = wmi.WMI(namespace="root\\cimv2")
# AMD GPU info via Win32_VideoController
for gpu in w.Win32_VideoController():
    print(f"{gpu.Name}: {gpu.AdapterRAM} bytes VRAM")
```

### Option B: AMD SMI (AMD System Management Interface)
```powershell
# If installed with Adrenalin driver, amdsmi may be available
# Check: https://github.com/ROCm/amdsmi
pip install amdsmi

# Usage:
python -c "
import amdsmi
amdsmi.amdsmi_init()
devices = amdsmi.amdsmi_get_processor_handles()
for dev in devices:
    temp = amdsmi.amdsmi_get_temp_metric(dev, amdsmi.AmdSmiTemperatureType.EDGE, amdsmi.AmdSmiTemperatureMetric.CURRENT)
    print(f'GPU Temp: {temp}C')
amdsmi.amdsmi_shut_down()
"
```

### Option C: amdgpu-sysmon or GPU-Z parsing
- **GPU-Z** shows real-time AMD GPU stats but has no Python API
- **LibreHardwareMonitor** exposes AMD GPU data via WMI (same as other Windows setups)
- This is the most reliable path for thermals + fan speed + VRAM usage

### For the TokenPal architecture
Create `tokenpal/senses/hardware/amd_gpu_hardware.py`:
- Extends `PsutilHardware`
- Uses `amdsmi` if available, falls back to WMI + LibreHardwareMonitor
- Registered with `platforms = ("windows",)` and detected via GPU vendor check at resolve time

---

## 7. LM Studio (HTTP backend — easiest)

LM Studio supports AMD GPUs via Vulkan:
1. Download from https://lmstudio.ai
2. It detects the 9070 XT and uses Vulkan backend
3. Download any GGUF model
4. Start local server → `http://localhost:1234/v1`

---

## 8. Verification Checklist

```powershell
# 1. AMD driver
# Device Manager -> Display adapters -> "AMD Radeon RX 9070 XT"

# 2. Python + venv
python --version                        # 3.12+

# 3. llama-server (recommended path)
curl http://localhost:11434/v1/models
# Should return JSON with your loaded GGUF model
# If using start-llamaserver.bat, run it first

# 4. TokenPal health check
tokenpal --validate
# Should show: llama-server reachable, model available

# 5. PyTorch ROCm (if installed)
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# 6. pywin32
python -c "import win32gui; print(win32gui.GetForegroundWindow())"

# 7. psutil
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 8. Screen capture
python -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 9. AMD GPU monitoring
python -c "
try:
    import amdsmi
    amdsmi.amdsmi_init()
    devs = amdsmi.amdsmi_get_processor_handles()
    print(f'AMD GPUs found: {len(devs)}')
    amdsmi.amdsmi_shut_down()
except ImportError:
    print('amdsmi not installed -- will use WMI/LibreHardwareMonitor fallback')
"
```

---

## 9. Tesseract OCR (optional)

```powershell
winget install UB-Mannheim.TesseractOCR
```

---

## 10. LibreHardwareMonitor (recommended — best AMD GPU monitoring)

- Download from: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases
- Run as admin
- Exposes AMD GPU temperature, fan RPM, VRAM usage, clock speeds via WMI
- This is the most reliable cross-vendor monitoring path on Windows

---

## 11. Known Gotchas

- **Ollama is broken on RDNA 4.** Vulkan produces wrong numerics on dense models; ROCm can't enumerate gfx1201. Use the llama.cpp-direct path (Option A). See [docs/amd-dgpu-setup.md](amd-dgpu-setup.md) for the full story.
- **SmartScreen on first launch.** Downloaded `llama-server.exe` trips Windows SmartScreen. Click "More info -> Run anyway". No codesigned build is available upstream.
- **No pynvml.** Don't install it -- it'll import but crash when trying to talk to an AMD GPU. Use `amdsmi` or WMI instead.
- **PyTorch ROCm on Windows:** Works but wheels are larger and less frequently updated than CUDA wheels. Check https://pytorch.org/get-started/locally/ for current availability.
- **VRAM headroom:** 16 GB sounds generous, but Windows + Adrenalin driver + desktop compositor eat ~1-2 GB. Budget 14 GB for models. See the VRAM tier table in [docs/amd-dgpu-setup.md](amd-dgpu-setup.md).
- **FP8 native support:** The 9070 XT has native FP8 WMMA instructions. If llama.cpp/vLLM support FP8 quantization for RDNA 4, you could run even larger models. Cutting edge -- check llama.cpp releases for RDNA 4 FP8 support.
- **Desktop thermals are a non-issue** compared to laptops. No throttling to worry about.

---

## 12. What This Machine Is Best For

This is your **power station** for TokenPal development:
- **Run the biggest models:** gemma4:26b MoE fits in 16 GB VRAM with room to spare. 3.8B active params = fast inference with 26B knowledge.
- **Vision model headroom:** Can load a chat LLM (8B, ~5 GB) AND a vision model (Florence-2, ~1 GB) simultaneously.
- **Fast iteration:** Desktop CPU + GPU = fastest build/test cycles.
- **No battery concerns:** Can run inference flat-out without worrying about power efficiency.
- **TokenPal inference server:** Serves the whole LAN via `scripts/install-windows.ps1 -Mode Server`. Mac clients point at `http://apollyon:8585/v1`.

Point your thin clients (laptops, Mac) at this box via the HTTP backend and let it do the heavy lifting.
