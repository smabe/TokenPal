#!/usr/bin/env python3
"""TokenPal setup script — gets you from fresh clone to running in one command.

Usage:
    python3 setup_tokenpal.py
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── Helpers ──────────────────────────────────────────────────────────────────

BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"
CHECK = f"{GREEN}✓{RESET}"
WARN = f"{YELLOW}!{RESET}"
FAIL = f"{RED}✗{RESET}"

PROJECT_ROOT = Path(__file__).resolve().parent


def step(msg: str) -> None:
    print(f"\n{BOLD}▸ {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"  {CHECK} {msg}")


def warn(msg: str) -> None:
    print(f"  {WARN} {msg}")


def fail(msg: str) -> None:
    print(f"  {FAIL} {msg}")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def ask(prompt: str, default: str = "y") -> bool:
    hint = "[Y/n]" if default == "y" else "[y/N]"
    try:
        answer = input(f"  {prompt} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default == "y"
    if not answer:
        return default == "y"
    return answer.startswith("y")


# ── Platform detection ───────────────────────────────────────────────────────

def detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    elif system == "linux":
        return "linux"
    return system


def pip_extras(plat: str) -> str:
    extras = {"macos": "macos,dev", "windows": "windows,dev", "linux": "dev"}
    return extras.get(plat, "dev")


# ── Steps ────────────────────────────────────────────────────────────────────

def check_python() -> bool:
    step("Checking Python version")
    v = sys.version_info
    if v >= (3, 12):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    elif v >= (3, 10):
        warn(f"Python {v.major}.{v.minor} — 3.12+ recommended, but should work")
        return True
    else:
        fail(f"Python {v.major}.{v.minor} — need 3.10+")
        return False


def setup_venv() -> Path:
    step("Setting up virtual environment")
    venv_dir = PROJECT_ROOT / ".venv"

    # Already in a venv?
    if sys.prefix != sys.base_prefix:
        ok(f"Already in a virtual environment: {sys.prefix}")
        return Path(sys.prefix)

    if venv_dir.exists():
        ok(f"Virtual environment exists: {venv_dir}")
    else:
        print(f"  Creating virtual environment at {venv_dir}...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        ok("Created .venv")

    return venv_dir


def get_venv_python(venv_dir: Path) -> str:
    """Return the python executable inside the venv."""
    if sys.prefix != sys.base_prefix:
        return sys.executable
    if platform.system() == "Windows":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python3")


def install_deps(python: str, plat: str) -> bool:
    step("Installing dependencies")
    extras = pip_extras(plat)
    print(f"  Running: pip install -e \".[{extras}]\"")

    subprocess.run(
        [python, "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True, text=True,
    )

    result = subprocess.run(
        [python, "-m", "pip", "install", "-e", f".[{extras}]"],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode == 0:
        ok(f"Installed with [{extras}] extras")
        return True
    else:
        fail("pip install failed")
        print(result.stderr[-500:] if result.stderr else "")
        return False


def check_ollama() -> bool:
    step("Checking for Ollama")

    if shutil.which("ollama"):
        ok("Ollama is installed")

        # Check if it's running
        result = run(["ollama", "list"])
        if result.returncode == 0:
            ok("Ollama is running")
            # Check for default model
            if "gemma4" in (result.stdout or ""):
                ok("gemma4 model is available")
                return True
            else:
                warn("gemma4 not found")
                if ask("Pull gemma4 now? (~3GB download)"):
                    print("  Pulling gemma4 (this may take a few minutes)...")
                    pull = subprocess.run(
                        ["ollama", "pull", "gemma4"],
                        capture_output=False,
                    )
                    if pull.returncode == 0:
                        ok("Model pulled successfully")
                        return True
                    else:
                        fail("Pull failed — you can retry later: ollama pull gemma4")
                        return True  # Non-fatal
                return True
        else:
            warn("Ollama installed but not running")
            plat = detect_platform()
            if plat == "macos":
                print("  Start it with: brew services start ollama")
                print("  Or just run: ollama serve")
            elif plat == "windows":
                print("  Launch Ollama from the Start menu or run: ollama serve")
            else:
                print("  Run: ollama serve")
            return True  # Non-fatal
    else:
        warn("Ollama not found")
        plat = detect_platform()
        if plat == "macos":
            if shutil.which("brew"):
                if ask("Install Ollama via Homebrew?"):
                    print("  Installing...")
                    result = subprocess.run(["brew", "install", "ollama"], capture_output=False)
                    if result.returncode == 0:
                        ok("Ollama installed")
                        print("  Start it with: brew services start ollama")
                        return True
                    else:
                        fail("brew install failed")
            else:
                print("  Install from: https://ollama.com/download")
        elif plat == "windows":
            print("  Install with: winget install Ollama.Ollama")
            print("  Or download from: https://ollama.com/download")
        else:
            print("  Install from: https://ollama.com/download")
        return True  # Non-fatal


def setup_config() -> None:
    step("Setting up config")
    config_path = PROJECT_ROOT / "config.toml"
    default_path = PROJECT_ROOT / "config.default.toml"

    if config_path.exists():
        ok("config.toml already exists")
        return

    if not default_path.exists():
        fail("config.default.toml not found — is this the project root?")
        return

    shutil.copy(default_path, config_path)
    ok("Created config.toml from defaults")
    print("  Edit config.toml to customize model, senses, and UI settings.")


def check_permissions_macos() -> None:
    step("macOS permissions reminder")
    print("  TokenPal needs these permissions (grant on first run):")
    print("    - Screen Recording (for screen capture sense)")
    print("    - Accessibility (for idle detection / keyboard listener)")
    print("  Go to: System Settings → Privacy & Security")
    print("  Grant permissions to your terminal app (Terminal, iTerm2, Ghostty, etc.)")


def verify_install(python: str) -> bool:
    step("Verifying installation")
    result = subprocess.run(
        [python, "-c", "from tokenpal.app import main; print('OK')"],
        capture_output=True, text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode == 0 and "OK" in result.stdout:
        ok("TokenPal imports successfully")
        return True
    else:
        fail("Import check failed")
        if result.stderr:
            print(f"  {result.stderr.strip()[:200]}")
        return False


def print_summary(plat: str, venv_dir: Path) -> None:
    print(f"\n{BOLD}{'─' * 50}{RESET}")
    print(f"{BOLD}{GREEN}TokenPal is ready!{RESET}\n")

    # Activation command
    if sys.prefix == sys.base_prefix:
        if plat == "windows":
            print(f"  1. Activate venv:  .venv\\Scripts\\Activate.ps1")
        else:
            print(f"  1. Activate venv:  source .venv/bin/activate")

    print(f"  2. Start Ollama:   ollama serve  (if not already running)")
    print(f"  3. Run TokenPal:   python -m tokenpal")
    print(f"  4. Stop:           Ctrl+C")
    print(f"\n  Config:   {PROJECT_ROOT / 'config.toml'}")
    print(f"  Logs:     ~/.tokenpal/logs/tokenpal.log")
    print(f"  Voices:   python -m tokenpal.tools.train_voice --help")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{BOLD}TokenPal Setup{RESET}")
    print(f"{'─' * 50}")

    plat = detect_platform()
    ok(f"Platform: {plat} ({platform.machine()})")

    if not check_python():
        sys.exit(1)

    venv_dir = setup_venv()
    python = get_venv_python(venv_dir)

    if not install_deps(python, plat):
        sys.exit(1)

    check_ollama()
    setup_config()

    if plat == "macos":
        check_permissions_macos()

    verify_install(python)
    print_summary(plat, venv_dir)


if __name__ == "__main__":
    main()
