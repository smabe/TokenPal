# Dev Environment Setup — Windows + Intel Core Ultra (Dell XPS 16)

Target: Dell XPS 16 (2026), Intel Core Ultra, Intel Arc iGPU.

> **Inference target: HTTP backend to a remote GPU server.** TokenPal does not target the Intel AI Boost NPU — the ecosystem (OpenVINO EP, Phi Silica) is not a shipping path. On this machine, run TokenPal as a client against a remote llamacpp or Ollama server on your LAN, or fall back to a local Ollama install on CPU/Arc iGPU for small models.

---

## 1. Prerequisites

### Windows version
- Windows 11 23H2 or later
- Check: `winver` in Run dialog

### Python
- Python 3.12+
- Install from https://www.python.org/downloads/
- **Check "Add to PATH"** during install
- Verify: `python --version` and `pip --version`

### Git
- Install Git for Windows: https://git-scm.com/download/win
- Or via winget: `winget install Git.Git`

### Visual Studio Build Tools (needed for some pip packages)
- `winget install Microsoft.VisualStudio.2022.BuildTools`
- During install, select **"Desktop development with C++"** workload
- Needed for: `pywin32`, some native extensions

---

## 2. Python Environment

### Create project venv
```powershell
cd C:\Users\<you>\projects\TokenPal
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Core dependencies
Let the installer handle it:

```powershell
iwr -useb https://raw.githubusercontent.com/smabe/TokenPal/main/install.ps1 | iex
```

Pick **Client** at the prompt if you have a server on your LAN, otherwise **Both** for a self-contained Ollama install.

---

## 3. LLM Backend — HTTP (recommended)

### Remote server (recommended)
Point TokenPal at a GPU box on your network. Best path for this machine — the Arc iGPU is not a great LLM target and the Core Ultra chews battery on CPU inference.

```toml
# config.toml
[llm]
backend = "http"
api_url = "http://<server-host>:11434/v1"
```

See [remote server setup](server-setup.md).

### Local Ollama fallback
```powershell
winget install Ollama.Ollama
ollama pull qwen3:8b       # small enough to be usable on CPU/iGPU
ollama serve
# API at http://localhost:11434/v1
```

Expect slow generation — this is a fallback, not the primary path.

---

## 4. Verification Checklist

```powershell
# 1. Python + venv
python --version                        # 3.12+

# 2. pywin32
python -c "import win32gui; print(win32gui.GetForegroundWindow())"

# 3. psutil
python -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 4. Screen capture
python -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 5. TokenPal preflight
tokenpal --validate
```

---

## 5. Known Gotchas

- **pywin32 postinstall:** After `pip install pywin32`, run `python -m pywin32_postinstall -install` if imports fail.
- **WMI queries are slow.** Cache hardware readings and poll at longer intervals (5-10s) vs. app awareness (1-2s).
- **Screen capture permissions:** No extra permissions needed on Windows (unlike macOS).
- **Battery drain:** Local CPU inference on this chip hammers battery. Prefer the HTTP backend to a plugged-in server.
