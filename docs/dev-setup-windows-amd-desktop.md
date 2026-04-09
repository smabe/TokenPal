# Dev Environment Setup — Windows + Ryzen 9800X3D + Radeon RX 9070 XT (Desktop)

Target: AMD Ryzen 7 9800X3D (no NPU), Radeon RX 9070 XT (16 GB VRAM, RDNA 4). Desktop.

**Primary inference target: RX 9070 XT via ROCm HIP or Vulkan.** No NPU on this chip. The 16 GB VRAM makes this your most capable inference machine — can run 13B+ models comfortably.

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

## 4. LLM Backend — llama.cpp with ROCm HIP (Recommended)

### Option A: Pre-built ROCm binaries via Ollama (easiest)
```powershell
winget install Ollama.Ollama

# Ollama 0.16.2+ supports RX 9000 series via ROCm
# It auto-detects the 9070 XT
ollama pull phi3:mini
ollama pull llama3:8b
ollama pull llama3:13b          # 13B fits easily in 16 GB VRAM
ollama pull qwen2.5:14b         # another strong option
ollama serve
# API at http://localhost:11434/v1

# Verify GPU is being used:
ollama ps
# Should show the model loaded on GPU
```

### Option B: llama-cpp-python with ROCm
```powershell
# Install llama-cpp-python with ROCm/HIP support
# This requires HIP SDK to be installed first

# Check for pre-built ROCm wheels:
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/rocm624
# Replace rocm624 with your HIP SDK version (rocm630, etc.)

# If no pre-built wheel exists for your version, build from source:
# CMAKE_ARGS="-DGGML_HIP=ON" pip install llama-cpp-python --no-binary llama-cpp-python
# (This requires CMake and HIP SDK in PATH)

# Verify:
python -c "from llama_cpp import Llama; print('OK')"
```

### Option C: llama.cpp with Vulkan (fallback — no ROCm needed)
```powershell
# Vulkan works on any GPU without vendor-specific SDKs
# Install Vulkan SDK: https://vulkan.lunarg.com/sdk/home

# Build llama-cpp-python with Vulkan:
# CMAKE_ARGS="-DGGML_VULKAN=ON" pip install llama-cpp-python --no-binary llama-cpp-python

# Or use pre-built Vulkan wheels if available:
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/vulkan
```

**Recommendation:** Start with Ollama (Option A). If you need tighter integration, move to llama-cpp-python with ROCm (Option B). Vulkan (Option C) is the safe fallback if ROCm is giving trouble.

### Option D: PyTorch with ROCm
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
# Device Manager → Display adapters → "AMD Radeon RX 9070 XT"

# 2. HIP SDK (if using ROCm path)
hipcc --version
hipinfo

# 3. Python + venv
python --version                        # 3.12+

# 4. Ollama with GPU
ollama run phi3:mini "Say hello"
ollama ps                               # should show GPU usage

# 5. PyTorch ROCm (if installed)
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# 6. llama-cpp-python (if installed)
python -c "from llama_cpp import Llama; print('OK')"

# 7. pywin32
python -c "import win32gui; print(win32gui.GetForegroundWindow())"

# 8. psutil
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 9. Screen capture
python -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 10. AMD GPU monitoring
python -c "
try:
    import amdsmi
    amdsmi.amdsmi_init()
    devs = amdsmi.amdsmi_get_processor_handles()
    print(f'AMD GPUs found: {len(devs)}')
    amdsmi.amdsmi_shut_down()
except ImportError:
    print('amdsmi not installed — will use WMI/LibreHardwareMonitor fallback')
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

- **ROCm on Windows is less mature than CUDA.** Expect rougher edges. Ollama abstracts most of this away — start there.
- **HIP SDK version must match.** RDNA 4 (gfx1200) requires HIP SDK 6.3+. Older versions silently fall back to CPU.
- **Vulkan vs ROCm:** Vulkan is easier to set up but slower (~70-80% of ROCm HIP speed). Fine for a desktop buddy; matters more for throughput workloads.
- **No pynvml.** Don't install it — it'll import but crash when trying to talk to an AMD GPU. Use `amdsmi` or WMI instead.
- **PyTorch ROCm on Windows:** Works but wheels are larger and less frequently updated than CUDA wheels. Check https://pytorch.org/get-started/locally/ for current availability.
- **VRAM headroom:** 16 GB sounds generous, but Windows + Adrenalin driver + desktop compositor eat ~1-2 GB. Budget 14 GB for models.
- **FP8 native support:** The 9070 XT has native FP8 WMMA instructions. If llama.cpp/vLLM support FP8 quantization for RDNA 4, you could run even larger models. Cutting edge — check llama.cpp releases for RDNA 4 FP8 support.
- **No NPU.** The 9800X3D has no neural processor. Don't waste time looking for one. The 9070 XT is your only accelerator.
- **Desktop thermals are a non-issue** compared to laptops. No throttling to worry about.

---

## 12. What This Machine Is Best For

This is your **power station** for TokenPal development:
- **Run the biggest models:** 13B-14B Q4 fits in 16 GB VRAM. Higher quality commentary.
- **Vision model headroom:** Can load a chat LLM (8B, ~5 GB) AND a vision model (Florence-2, ~1 GB) simultaneously.
- **Fast iteration:** Desktop CPU + GPU = fastest build/test cycles.
- **No battery concerns:** Can run inference flat-out without worrying about power efficiency.

The laptop NPU story is about efficiency; this machine is about capability.
