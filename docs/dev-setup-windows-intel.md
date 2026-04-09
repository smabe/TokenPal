# Dev Environment Setup — Windows + Intel Core Ultra (Dell XPS 16)

Target: Dell XPS 16 (2026), Intel Core Ultra, Intel AI Boost NPU, Copilot+ PC.

---

## 1. Prerequisites

### Windows version
- Windows 11 24H2 or later (required for Windows AI APIs / Copilot+ features)
- Check: `winver` in Run dialog

### Python
- Python 3.12+ (3.12 recommended for ARM64 compatibility if applicable)
- Install from https://www.python.org/downloads/
- **Check "Add to PATH"** during install
- Verify: `python --version` and `pip --version`

### Git
- Install Git for Windows: https://git-scm.com/download/win
- Or via winget: `winget install Git.Git`

### Visual Studio Build Tools (needed for some pip packages)
- `winget install Microsoft.VisualStudio.2022.BuildTools`
- During install, select **"Desktop development with C++"** workload
- Needed for: `pynvml`, `pywin32`, some ONNX Runtime builds

---

## 2. Intel NPU Setup

### Install Intel NPU driver
1. Download latest from: https://www.intel.com/content/www/us/en/download/794734/intel-npu-driver-windows.html
2. Install and reboot
3. Verify in Device Manager: should see "Intel(R) AI Boost" under "Neural processors"
4. Verify in Task Manager: Performance tab should show "NPU 0"

### Verify NPU driver via PowerShell
```powershell
# List NPU devices
Get-PnpDevice | Where-Object { $_.FriendlyName -like '*NPU*' -or $_.FriendlyName -like '*AI Boost*' }
```

---

## 3. Windows AI APIs (Copilot+ Features)

### Check availability
```powershell
# Check if Windows AI capabilities are installed
Get-WindowsCapability -Online | Where-Object Name -like '*Windows.AI*'
```

### Install Windows AI Dev Gallery
- Microsoft Store: search "AI Dev Gallery" or visit https://apps.microsoft.com/detail/ai-dev-gallery
- This tests which APIs (Phi Silica, OCR, Image Description) work on your exact SKU
- **Run it first** — the results determine which LLM backend you'll use

### If Phi Silica is available
You can use the `Microsoft.Windows.AI.Generative` WinRT API from Python via `winrt`.
This is the zero-setup path — no model downloads needed.

### If Phi Silica is NOT available on Intel yet
Fall back to OpenVINO GenAI or the HTTP backend (LM Studio/Ollama). See LLM backend section below.

---

## 4. Python Environment

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

# Hardware monitoring (NVIDIA not applicable here, but WMI is)
pip install wmi

# Config
# tomllib is stdlib in 3.11+, tomli for writing
pip install tomli-w

# LLM backends (install the one you'll use)
pip install onnxruntime-genai          # For ONNX Runtime + NPU
# OR
pip install llama-cpp-python           # For llama.cpp (CPU fallback)

# Windows-specific senses
pip install winrt-Windows.Media.Control  # Music detection (SMTC)
pip install winrt-Windows.AI.MachineLearning  # If using Windows ML

# Screen reading (Tier 2)
pip install pytesseract                # OCR fallback (needs Tesseract binary)

# Web search (on-demand)
pip install duckduckgo-search

# Voice (optional)
pip install pyttsx3                    # TTS via Windows SAPI

# Dev tools
pip install pytest pytest-asyncio ruff mypy
```

### ONNX Runtime with OpenVINO EP (NPU inference)
```powershell
# This is the key package for Intel NPU inference
pip install onnxruntime-openvino

# Verify NPU is available as an execution provider
python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Should include 'OpenVINOExecutionProvider'
```

### OpenVINO GenAI (alternative LLM path if Phi Silica unavailable)
```powershell
pip install openvino-genai

# Download a model (e.g., Phi-3-mini INT4 for NPU)
# Models at: https://huggingface.co/OpenVINO
```

---

## 5. LM Studio / Ollama (HTTP backend — easiest onboarding)

If ONNX/OpenVINO setup is painful, just run a local model server:

### LM Studio
1. Download from https://lmstudio.ai
2. Download Phi-3-mini-4k-instruct (GGUF format)
3. Start local server (defaults to `http://localhost:1234/v1`)
4. TokenPal hits it via `http_backend.py` — zero NPU config needed

### Ollama
```powershell
winget install Ollama.Ollama
ollama pull phi3:mini
ollama serve
# API at http://localhost:11434/v1
```

---

## 6. Verification Checklist

Run these after setup to confirm everything works:

```powershell
# 1. Python + venv
python --version                        # 3.12+
pip list | Select-String onnxruntime    # installed

# 2. NPU driver
# Check Task Manager → Performance → NPU 0 exists

# 3. ONNX Runtime providers
python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
# Expect: ['OpenVINOExecutionProvider', 'CPUExecutionProvider']

# 4. pywin32
python -c "import win32gui; print(win32gui.GetForegroundWindow())"

# 5. psutil
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 6. Screen capture
python -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 7. Windows AI APIs (if Copilot+)
# Run AI Dev Gallery and note which features are NPU-backed vs CPU-fallback
```

---

## 7. Tesseract OCR (optional, for OCR sense fallback)

```powershell
winget install UB-Mannheim.TesseractOCR
# Add to PATH: C:\Program Files\Tesseract-OCR
# Verify: tesseract --version
```

---

## 8. LibreHardwareMonitor (optional, for deep thermals/fan data)

- Download from: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases
- Run as admin (needed for sensor access)
- Exposes WMI interface that `win32_hardware.py` queries
- Without it, `psutil` still gives CPU/RAM/disk but no temps or fan RPM on Windows

---

## 9. Known Gotchas

- **Intel NPU driver updates can break ONNX providers.** Pin your driver version once things work.
- **Windows AI APIs may require specific Windows Insider builds** for Intel support. Check release notes.
- **pywin32 postinstall:** After `pip install pywin32`, run `python -m pywin32_postinstall -install` if imports fail.
- **WMI queries are slow.** Cache hardware readings and poll at longer intervals (5-10s) vs. app awareness (1-2s).
- **Screen capture permissions:** No extra permissions needed on Windows (unlike macOS).
