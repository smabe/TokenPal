# Dev Environment Setup — Windows + AMD Ryzen 9 8945HS + RTX 4070 (Personal Laptop)

Target: AMD Ryzen 9 8945HS, RTX 4070 Laptop GPU (8GB VRAM), 32GB RAM, AMD XDNA NPU (16 TOPS).

**Primary inference target: RTX 4070 via CUDA.** The XDNA NPU at 16 TOPS is below Copilot+ threshold — no Windows AI APIs. Use it for tiny background tasks at best.

---

## 1. Prerequisites

### Windows version
- Windows 11 23H2 or later
- Check: `winver` in Run dialog

### Python
- Python 3.12+ (x86_64 — this is NOT an ARM machine)
- Install from https://www.python.org/downloads/
- **Check "Add to PATH"** during install
- Verify: `python --version` and `pip --version`

### Git
- `winget install Git.Git`

### Visual Studio Build Tools
- `winget install Microsoft.VisualStudio.2022.BuildTools`
- Select **"Desktop development with C++"** workload
- Required for CUDA-enabled Python packages

---

## 2. NVIDIA CUDA Setup (Critical — this is your main inference engine)

### Install NVIDIA GPU driver
1. Download latest Game Ready or Studio driver from https://www.nvidia.com/Download/index.aspx
2. Select: RTX 4070 Laptop GPU → Windows 11
3. Install and reboot
4. Verify: `nvidia-smi` in terminal should show RTX 4070, CUDA version, VRAM

### Install CUDA Toolkit
```powershell
# Check which CUDA version nvidia-smi reports (top right), install matching toolkit
# Download from: https://developer.nvidia.com/cuda-downloads
# CUDA 12.x recommended (12.4+ for latest PyTorch/llama.cpp)

# After install, verify:
nvcc --version
```

### Install cuDNN (needed for some frameworks)
1. Download from: https://developer.nvidia.com/cudnn (requires NVIDIA account)
2. Extract and copy to CUDA toolkit directory
3. Or install via pip with compatible packages (PyTorch bundles its own)

---

## 3. AMD XDNA NPU Setup (Optional — low priority)

The NPU is only 16 TOPS and can't run Copilot+ features, but you can experiment with small models.

### Install AMD NPU driver
- Should come with AMD Adrenalin driver package
- Verify in Task Manager: Performance → NPU 0 shows "AMD Radeon NPU Compute Accelerator Device"
- You already have this confirmed from the screenshot

### Ryzen AI Software SDK (optional exploration)
```powershell
# Download from: https://www.amd.com/en/developer/resources/ryzen-ai-software.html
# Includes Vitis AI Execution Provider for ONNX Runtime
# Only worth exploring for tiny INT8 models (classifiers, keyword detection)
```

---

## 4. Python Environment

### Create project venv
```powershell
cd C:\Users\<you>\projects\windoze
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Core dependencies
```powershell
pip install --upgrade pip

# Core
pip install psutil pywin32 mss pyperclip pynput

# Hardware monitoring
pip install wmi pynvml                  # pynvml is critical here — RTX 4070 monitoring
pip install GPUtil                      # alternative GPU util (simpler API)

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

### LLM backend — llama-cpp-python with CUDA (recommended)
```powershell
# Install llama-cpp-python with CUDA support
# This requires CUDA toolkit to be installed first
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
# Replace cu124 with your CUDA version (cu121, cu122, cu123, cu124)

# Verify CUDA is available
python -c "from llama_cpp import Llama; print('llama.cpp loaded')"
```

### LLM backend — PyTorch + transformers (alternative for vision models)
```powershell
# Install PyTorch with CUDA
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# Verify CUDA
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, Device: {torch.cuda.get_device_name(0)}')"
# Expect: CUDA: True, Device: NVIDIA GeForce RTX 4070 Laptop GPU

# For vision models (Florence-2, Moondream, Qwen2.5-VL)
pip install transformers accelerate pillow
```

### ONNX Runtime with CUDA EP (alternative)
```powershell
pip install onnxruntime-gpu
python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Expect: ['CUDAExecutionProvider', 'CPUExecutionProvider']
```

---

## 5. Model Downloads

### For llama.cpp (GGUF format)
```powershell
# Option A: Use LM Studio GUI to download models

# Option B: Manual download from HuggingFace
pip install huggingface-hub
huggingface-cli download microsoft/Phi-3-mini-4k-instruct-gguf Phi-3-mini-4k-instruct-q4.gguf --local-dir models/

# Recommended starter models (fit in 8GB VRAM):
# - Phi-3-mini-4k-instruct Q4_K_M (~2.3 GB) — fast, good for commentary
# - Llama-3-8B-Instruct Q4_K_M (~4.7 GB) — better quality, still fits
# - Qwen2.5-7B-Instruct Q4_K_M (~4.4 GB) — strong alternative
```

### For vision (PyTorch/transformers)
```python
# Florence-2 (~0.5 GB) — good small VLM for screen description
# Downloaded automatically on first use:
# from transformers import AutoProcessor, AutoModelForCausalLM
# model = AutoModelForCausalLM.from_pretrained("microsoft/Florence-2-base", trust_remote_code=True)

# Moondream2 (~1.6 GB) — another small VLM option
# model = AutoModelForCausalLM.from_pretrained("vikhyatk/moondream2", trust_remote_code=True)
```

---

## 6. LM Studio / Ollama (HTTP backend — easiest path)

### LM Studio
1. Download from https://lmstudio.ai
2. It auto-detects RTX 4070 and uses CUDA
3. Download Phi-3-mini-4k-instruct
4. Start local server → `http://localhost:1234/v1`
5. Windoze hits it via `http_backend.py`

### Ollama
```powershell
winget install Ollama.Ollama
ollama pull phi3:mini          # auto-uses GPU
ollama pull llama3:8b          # if you want something beefier
ollama serve
# API at http://localhost:11434/v1
```

---

## 7. Verification Checklist

```powershell
# 1. NVIDIA driver + CUDA
nvidia-smi
# Should show: RTX 4070, CUDA 12.x, 8GB VRAM

# 2. Python + venv
python --version                        # 3.12+

# 3. PyTorch CUDA
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# True NVIDIA GeForce RTX 4070 Laptop GPU

# 4. llama.cpp
python -c "from llama_cpp import Llama; print('OK')"

# 5. pynvml (GPU monitoring)
python -c "import pynvml; pynvml.nvmlInit(); h = pynvml.nvmlDeviceGetHandleByIndex(0); print(pynvml.nvmlDeviceGetName(h))"

# 6. pywin32
python -c "import win32gui; print(win32gui.GetForegroundWindow())"

# 7. psutil
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 8. Screen capture
python -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 9. NPU (optional)
# Task Manager → Performance → NPU 0 should show AMD Radeon NPU Compute Accelerator Device
```

---

## 8. Tesseract OCR (optional)

```powershell
winget install UB-Mannheim.TesseractOCR
# Add to PATH: C:\Program Files\Tesseract-OCR
```

---

## 9. LibreHardwareMonitor (for deep thermals)

Same as Intel setup:
- Download from: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases
- Run as admin for full sensor access
- WMI interface exposes CPU temps, GPU temps, fan RPMs

---

## 10. Known Gotchas

- **VRAM budget:** RTX 4070 Laptop has 8 GB VRAM. A Q4 7B model uses ~4.5 GB, leaving room for a small vision model OR the OS, not both. Plan model loading carefully.
- **llama-cpp-python CUDA wheels:** Must match your CUDA toolkit version exactly. If `pip install` gives CPU-only builds, you need the `--extra-index-url` flag.
- **Dual GPU confusion:** This machine has both Radeon 780M iGPU and RTX 4070 dGPU. Make sure frameworks target the 4070 (device index 0 in CUDA, but verify with `nvidia-smi`). The 780M is irrelevant.
- **Power modes:** On battery, Windows may throttle the RTX 4070 or disable it entirely. Test plugged in first. For battery usage, the weak NPU or CPU fallback may actually be useful.
- **pynvml requires admin** for some sensor queries (power draw, thermals). Run terminal as admin for full monitoring.
- **WMI + pynvml together:** Don't query both at high frequency — they're both slow. Cache readings.
