#!/usr/bin/env python3
"""TokenPal setup script — gets you from fresh clone to running in one command.

Usage:
    python3 setup_tokenpal.py            # auto-detect (default)
    python3 setup_tokenpal.py --local    # full local install + Ollama
    python3 setup_tokenpal.py --client   # client-only (remote GPU server)
"""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

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


def pip_extras(plat: str, headless: bool = False) -> str:
    extras = {"macos": "macos,dev", "windows": "windows,dev", "linux": "dev"}
    base = extras.get(plat, "dev")
    if headless:
        return base
    return f"{base},desktop"


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


def install_deps(python: str, plat: str, headless: bool = False) -> bool:
    step("Installing dependencies")
    extras = pip_extras(plat, headless=headless)
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


def setup_config(mode: str) -> None:
    step("Setting up config")
    config_path = PROJECT_ROOT / "config.toml"
    default_path = PROJECT_ROOT / "config.default.toml"

    if config_path.exists():
        ok("config.toml already exists")
    elif not default_path.exists():
        fail("config.default.toml not found — is this the project root?")
        return
    else:
        shutil.copy(default_path, config_path)
        ok("Created config.toml from defaults")

    if mode == "client":
        _configure_client(config_path)


def _configure_client(config_path: Path) -> None:
    """Prompt for remote server URL and write it to config.toml."""
    step("Configuring remote server")
    print("  TokenPal will connect to a remote GPU server for inference.")
    print("  Example: http://gpu-box.local:8585")

    if not sys.stdin.isatty():
        warn("Non-interactive — set [server] in config.toml manually")
        return

    try:
        url = input("  Server URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        warn("Skipped — set [server] in config.toml manually")
        return

    if not url:
        warn("Skipped — set [server] in config.toml manually")
        return

    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8585

    server_block = f'[server]\nhost = "{host}"\nport = {port}\nmode = "remote"\n'

    content = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if re.search(r"^\[server\]\s*$", content, re.MULTILINE):
        content = re.sub(
            r"^\[server\].*?(?=\n\[|\Z)",
            server_block.rstrip(),
            content,
            flags=re.DOTALL | re.MULTILINE,
        )
    else:
        content = content.rstrip() + "\n\n" + server_block

    config_path.write_text(content, encoding="utf-8")
    ok(f"Server configured: {host}:{port}")
    print("  TokenPal will use the remote server for LLM inference.")


def check_permissions_macos() -> None:
    step("macOS permissions reminder")
    print("  TokenPal needs Accessibility permission (for idle detection).")
    print("  Go to: System Settings → Privacy & Security → Accessibility")
    print("  Grant permission to your terminal app (Terminal, iTerm2, Ghostty, etc.)")


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

    print(f"  {BOLD}Tip:{RESET} For fresh machine setup (installs Python, Ollama, everything):")
    print("    macOS:   bash scripts/install-macos.sh")
    print("    Windows: powershell scripts/install-windows.ps1")
    print("    Linux:   bash scripts/install-linux.sh")
    print()

    # Activation command
    if sys.prefix == sys.base_prefix:
        if plat == "windows":
            print("  1. Activate venv:  .venv\\Scripts\\Activate.ps1")
        else:
            print("  1. Activate venv:  source .venv/bin/activate")

    print("  2. Start Ollama:   ollama serve  (if not already running)")
    print("  3. Run TokenPal:   tokenpal")
    print("  4. Health check:   tokenpal --check")
    print("  5. Stop:           Ctrl+C")
    print(f"\n  Config:   {PROJECT_ROOT / 'config.toml'}")
    print("  Logs:     ~/.tokenpal/logs/tokenpal.log")
    print()
    print(f"  {BOLD}On first run, TokenPal will walk you through a quick setup wizard.{RESET}")
    print()


# ── Argument parsing ─────────────────────────────────────────────────────────

def parse_setup_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TokenPal setup — from fresh clone to running in one command.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--local", action="store_const", dest="mode", const="local",
        help="full local install (Ollama + model download)",
    )
    group.add_argument(
        "--client", action="store_const", dest="mode", const="client",
        help="client-only install (skip Ollama, configure remote server)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="skip the Qt desktop extra — terminal UI only",
    )
    parser.set_defaults(mode="default")
    return parser.parse_args()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_setup_args()
    mode = args.mode

    print(f"\n{BOLD}TokenPal Setup{RESET}")
    if mode != "default":
        print(f"Mode: {mode}")
    print(f"{'─' * 50}")

    plat = detect_platform()
    ok(f"Platform: {plat} ({platform.machine()})")

    if not check_python():
        sys.exit(1)

    venv_dir = setup_venv()
    python = get_venv_python(venv_dir)

    if not install_deps(python, plat, headless=args.headless):
        sys.exit(1)

    if mode != "client":
        check_ollama()

    setup_config(mode)

    if plat == "macos":
        check_permissions_macos()

    verify_install(python)
    print_summary(plat, venv_dir)


if __name__ == "__main__":
    main()
