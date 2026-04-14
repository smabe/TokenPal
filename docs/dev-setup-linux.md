# Dev Environment Setup — Linux

Target: Any Linux distro as a TokenPal daily-driver client. Ollama for LLM inference.

---

## 1. Prerequisites

### Python 3.12+, pip, venv, git

**Debian/Ubuntu:**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git
```

**Fedora:**
```bash
sudo dnf install python3 python3-pip git
```

**Arch:**
```bash
sudo pacman -S python python-pip git
```

Verify:
```bash
python3 --version   # 3.12+
git --version
```

---

## 2. Python Environment

```bash
cd ~/projects/TokenPal
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

Or use the setup script:
```bash
python3 setup_tokenpal.py
```

---

## 3. Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull gemma4
```

Verify:
```bash
ollama list   # should show gemma4
curl -s http://localhost:11434/v1/models | python3 -m json.tool
```

---

## 4. Sense Support

| Sense | Status | Notes |
|-------|--------|-------|
| hardware | Works | psutil, cross-platform |
| idle | Works | pynput, needs X11/Wayland (see below) |
| time_awareness | Works | stdlib |
| weather | Works | HTTP, opt-in via `/zip` command |
| git | Works | needs `git` in PATH |
| productivity | Works | sqlite3, stdlib |
| app_awareness | Not available | no Linux implementation |
| music | Not available | macOS only (AppleScript) |

### pynput on X11 vs Wayland

pynput uses Xlib under the hood. On X11, it works out of the box. On Wayland, you may need `XWayland` or the `DISPLAY` environment variable set. If idle detection doesn't work:

```bash
# Check your display server
echo $XDG_SESSION_TYPE   # x11 or wayland

# If Wayland, ensure XWayland is running and DISPLAY is set
echo $DISPLAY   # should be :0 or similar
```

Some Wayland compositors (Sway, Hyprland) don't expose input events to Xlib at all — idle detection won't work there. The buddy still runs fine, it just can't detect idle state.

---

## 5. NVIDIA GPU Monitoring (optional)

If you have an NVIDIA GPU and want hardware sense to report GPU stats:

```bash
pip install tokenpal[nvidia]
```

This pulls in `pynvml` for GPU utilization, temperature, and VRAM reporting.

---

## 6. Verification

```bash
# Quick health check
tokenpal --check

# Full preflight
tokenpal --validate

# Manual checks
python3 -c "from tokenpal.app import main; print('OK')"
python3 -c "import psutil; print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')"
python3 -c "import pynput; print('pynput OK')"
```

---

## 7. Running

```bash
source .venv/bin/activate
tokenpal              # normal mode
tokenpal --verbose    # debug logs in terminal
```

On first run, the setup wizard walks you through voice selection and optional weather setup.

---

## 8. Known Limitations

- **No app awareness:** There's no Linux implementation for detecting the active window/app. The buddy won't comment on what you're working in.
- **No music sense:** Music detection uses macOS AppleScript. No Linux equivalent is implemented.
- **Wayland idle detection:** pynput relies on Xlib, which doesn't work on pure Wayland compositors. X11 or XWayland required.
- **No overlay:** The Textual TUI is the only UI mode on Linux (which is the default anyway).
- **Accessibility permissions:** Unlike macOS, Linux doesn't gate input monitoring behind a permissions prompt — pynput just works on X11. Some distros may restrict `/dev/input` access; add your user to the `input` group if needed: `sudo usermod -aG input $USER`.
