# Dev Environment Setup — macOS + Apple Silicon (M-series)

Target: Any Mac with Apple Silicon (M1/M2/M3/M4/M5). MLX for LLM inference, pyobjc for native overlay.

---

## 1. Prerequisites

### macOS version
- macOS 14 Sonoma or later (15 Sequoia recommended for latest ML APIs)
- Check: Apple menu → About This Mac

### Verify Apple Silicon
```bash
uname -m
# Should output: arm64
```

### Xcode Command Line Tools
```bash
xcode-select --install
# Required for building Python packages with C extensions
```

### Homebrew
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Python
```bash
# Recommended: install via Homebrew for ARM64 native build
brew install python@3.12

# Verify it's ARM64 native (not Rosetta)
python3 --version
file $(which python3)
# Should show: Mach-O 64-bit executable arm64
```

### Git
```bash
# Included with Xcode CLT, or:
brew install git
```

---

## 2. Python Environment

### Create project venv
```bash
cd ~/projects/TokenPal
python3 -m venv .venv
source .venv/bin/activate
```

### Core dependencies
```bash
pip install --upgrade pip

# Core
pip install psutil mss pyperclip pynput

# macOS native bridge (critical for overlay + system APIs)
pip install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz
# pyobjc-framework-Cocoa: NSWindow, NSWorkspace, NSPasteboard
# pyobjc-framework-Quartz: CGWindowList for screen capture, CGEvent for idle detection

# Config
# tomllib is stdlib in 3.11+
pip install tomli-w

# Web search (on-demand)
pip install duckduckgo-search

# Dev tools
pip install pytest pytest-asyncio ruff mypy
```

---

## 3. LLM Backend — MLX (Recommended for Apple Silicon)

MLX is Apple's machine learning framework, purpose-built for Apple Silicon's unified memory architecture. This is the preferred path.

```bash
# Install MLX and MLX-LM
pip install mlx mlx-lm

# Verify MLX can see the GPU
python3 -c "import mlx.core as mx; print(mx.default_device())"
# Should output: Device(gpu, 0)

# Download a model
mlx_lm.convert --hf-path microsoft/Phi-3-mini-4k-instruct -q --q-bits 4
# Or use pre-converted models from HuggingFace:
# https://huggingface.co/mlx-community
```

### Pre-converted MLX models (easier)
```bash
pip install huggingface-hub

# Phi-3-mini (recommended starter)
huggingface-cli download mlx-community/Phi-3-mini-4k-instruct-4bit --local-dir models/phi3-mini

# Llama-3-8B (beefier, needs 6+ GB RAM for the model)
huggingface-cli download mlx-community/Meta-Llama-3-8B-Instruct-4bit --local-dir models/llama3-8b

# Whisper (for voice sense)
pip install mlx-whisper
```

### Quick test
```python
from mlx_lm import load, generate

model, tokenizer = load("mlx-community/Phi-3-mini-4k-instruct-4bit")
response = generate(model, tokenizer, prompt="Say hello in 5 words", max_tokens=20)
print(response)
```

---

## 4. LLM Backend — llama.cpp with Metal (Alternative)

If you prefer llama.cpp (GGUF models, same format as Windows):

```bash
# Install with Metal support (automatic on macOS ARM64)
pip install llama-cpp-python

# Verify Metal backend
python3 -c "from llama_cpp import Llama; print('OK')"

# Download a GGUF model
huggingface-cli download microsoft/Phi-3-mini-4k-instruct-gguf Phi-3-mini-4k-instruct-q4.gguf --local-dir models/
```

---

## 5. LM Studio / Ollama (HTTP backend — easiest)

### Ollama (recommended on macOS)
```bash
brew install ollama
ollama pull phi3:mini
ollama serve
# API at http://localhost:11434/v1
```

### LM Studio
- Download from https://lmstudio.ai (Apple Silicon native build)
- Auto-detects Metal, uses GPU
- Local server at `http://localhost:1234/v1`

---

## 6. macOS-Specific APIs via pyobjc

### Screen capture
```python
import Quartz

# Capture entire screen
image = Quartz.CGWindowListCreateImage(
    Quartz.CGRectInfinite,
    Quartz.kCGWindowListOptionOnScreenOnly,
    Quartz.kCGNullWindowID,
    Quartz.kCGWindowImageDefault,
)
```
**Important:** Requires **Screen Recording permission** in System Settings → Privacy & Security → Screen Recording. The app will prompt on first use. During development, grant permission to Terminal / your IDE.

### App awareness (foreground app + window title)
```python
from AppKit import NSWorkspace

ws = NSWorkspace.sharedWorkspace()
app = ws.frontmostApplication()
print(app.localizedName())  # e.g. "Safari"

# Window titles via Quartz
import Quartz
windows = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionOnScreenOnly,
    Quartz.kCGNullWindowID,
)
for w in windows:
    print(w.get('kCGWindowOwnerName'), w.get('kCGWindowName'))
```

### Clipboard
```python
from AppKit import NSPasteboard

pb = NSPasteboard.generalPasteboard()
text = pb.stringForType_("public.utf8-plain-text")
```

### Idle detection
```python
import Quartz

idle_seconds = Quartz.CGEventSourceSecondsSinceLastEventType(
    Quartz.kCGEventSourceStateCombinedSessionState,
    Quartz.kCGAnyInputEventType,
)
```

### Music detection
```bash
# Via osascript (works with Music.app and Spotify)
osascript -e 'tell application "Spotify" to get name of current track'
osascript -e 'tell application "Music" to get name of current track'
```
Or use the macOS MediaRemote private framework via ctypes (more complex, less stable).

### TTS
```python
from AppKit import NSSpeechSynthesizer

synth = NSSpeechSynthesizer.alloc().init()
synth.startSpeakingString_("Hello from TokenPal")
```
Or simpler: `subprocess.run(["say", "Hello from TokenPal"])`

### Overlay window (NSWindow — for full-screen support)
```python
from AppKit import (
    NSWindow, NSApplication, NSFloatingWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSBorderlessWindowMask,
)

# This is the skeleton — full implementation in macos_overlay.py
# Key properties:
#   window.setLevel_(NSFloatingWindowLevel)
#   window.setCollectionBehavior_(
#       NSWindowCollectionBehaviorCanJoinAllSpaces |
#       NSWindowCollectionBehaviorFullScreenAuxiliary |
#       NSWindowCollectionBehaviorStationary
#   )
#   window.setOpaque_(False)
#   window.setBackgroundColor_(NSColor.clearColor())
#   window.setIgnoresMouseEvents_(True)  # click-through
```

---

## 7. Hardware Monitoring on macOS

### psutil (cross-platform basics)
```python
import psutil

psutil.cpu_percent(interval=1)
psutil.virtual_memory().percent
psutil.sensors_battery()          # battery info
psutil.disk_io_counters()
psutil.net_io_counters()
```

### Thermals + power (requires sudo)
```bash
# powermetrics gives detailed CPU/GPU/ANE power and thermal data
sudo powermetrics --samplers cpu_power,gpu_power,ane_power,thermal -n 1 -i 1000
```
From Python (needs to run with elevated privileges or parse cached output):
```python
import subprocess
result = subprocess.run(
    ["sudo", "powermetrics", "--samplers", "cpu_power,thermal", "-n", "1", "-i", "1000"],
    capture_output=True, text=True,
)
# Parse result.stdout for thermal and power data
```

**Gotcha:** `powermetrics` requires root. Options:
1. Run TokenPal with sudo (not ideal)
2. Run a small helper daemon that caches sensor data to a file
3. Skip deep thermals and stick to psutil basics
4. Use `iStats` gem: `gem install iStats && istats` (less data but no sudo)

### GPU/ANE monitoring
- macOS does not expose per-process GPU utilization like NVIDIA does
- Activity Monitor shows GPU usage but there's no public API
- `powermetrics` with `gpu_power` and `ane_power` samplers shows aggregate power draw
- For the buddy, "GPU is busy" is enough — no need for per-process breakdown

---

## 8. Tesseract OCR (optional)

```bash
brew install tesseract
python3 -c "import pytesseract; print(pytesseract.get_tesseract_version())"
```

---

## 9. Whisper for STT (via MLX)

```bash
pip install mlx-whisper

# Quick test
python3 -c "
import mlx_whisper
result = mlx_whisper.transcribe('test.wav', path_or_hf_repo='mlx-community/whisper-tiny')
print(result['text'])
"
```

MLX Whisper is very fast on Apple Silicon — Whisper-tiny runs near real-time, Whisper-base is still usable.

---

## 10. Verification Checklist

```bash
# 1. Apple Silicon
uname -m                                # arm64

# 2. Python (ARM64 native)
python3 --version                       # 3.12+
file $(which python3)                   # arm64

# 3. MLX
python3 -c "import mlx.core as mx; print(mx.default_device())"
# Device(gpu, 0)

# 4. MLX-LM model loading
python3 -c "from mlx_lm import load; model, tok = load('mlx-community/Phi-3-mini-4k-instruct-4bit'); print('Model loaded')"

# 5. pyobjc
python3 -c "from AppKit import NSWorkspace; print(NSWorkspace.sharedWorkspace().frontmostApplication().localizedName())"

# 6. Screen capture (will prompt for permission)
python3 -c "import mss; sct = mss.mss(); print(sct.monitors)"

# 7. psutil
python3 -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"

# 8. Idle detection
python3 -c "import Quartz; print(f'Idle: {Quartz.CGEventSourceSecondsSinceLastEventType(1, 4294967295):.0f}s')"

# 9. Clipboard
python3 -c "from AppKit import NSPasteboard; print(NSPasteboard.generalPasteboard().stringForType_('public.utf8-plain-text'))"

# 10. TTS
python3 -c "import subprocess; subprocess.run(['say', 'TokenPal is alive'])"
```

---

## 11. macOS Permissions to Grant

The buddy needs these permissions in **System Settings → Privacy & Security**:

| Permission | Why | When prompted |
|---|---|---|
| **Screen Recording** | Screen capture sense | First time `mss` or `CGWindowListCreateImage` runs |
| **Accessibility** | Global hotkeys via `pynput`, idle detection | First time `pynput` listener starts |
| **Microphone** | Voice/STT sense | First time audio capture starts |
| **Input Monitoring** | Keyboard shortcuts | May be needed for global hotkey capture |

Grant these to **Terminal.app** (or your IDE) during development. For a packaged app, you'd grant to the app bundle itself.

---

## 12. Known Gotchas

- **Rosetta Python:** If `file $(which python3)` shows `x86_64`, you're running under Rosetta. MLX will not use the GPU. Reinstall ARM64 native Python via Homebrew.
- **pyobjc is large:** `pip install pyobjc` installs ALL frameworks (~200 MB). Install only what you need: `pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`.
- **Screen Recording permission resets:** macOS may revoke screen recording permission after OS updates. Re-grant if captures suddenly return blank images.
- **NSWindow on main thread:** AppKit requires all UI work on the main thread. The `macos_overlay.py` must use `performSelectorOnMainThread:` or `dispatch_async(dispatch_get_main_queue(), ...)` for updates from the brain thread.
- **powermetrics sudo:** No good workaround. Either skip deep thermals or accept the sudo requirement. Activity Monitor manages without sudo by using private entitlements.
- **Unified Memory:** Unlike Windows where GPU has separate VRAM, Apple Silicon shares RAM between CPU/GPU/ANE. A 7B model at Q4 uses ~4.5 GB of system RAM. On 8 GB machines, stick to 3B models.
- **tkinter on macOS:** Works but looks dated. The NSWindow overlay via pyobjc will look significantly better and handle full-screen apps properly. Use tkinter only as a cross-platform fallback.
