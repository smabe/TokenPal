"""First-run welcome wizard — shown once on fresh installs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tokenpal.cli import _BOLD, _GREEN, _RESET

_DIM = "\033[2m"
_MARKER_NAME = ".first_run_done"


def needs_first_run(data_dir: Path) -> bool:
    """Return True if the first-run wizard hasn't been completed."""
    return not (data_dir / _MARKER_NAME).exists()


def mark_first_run_done(data_dir: Path) -> None:
    """Write the marker file so the wizard doesn't run again."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _MARKER_NAME).write_text("done\n")


def run_wizard(data_dir: Path) -> None:
    """Interactive first-run wizard. Runs on the main thread before the overlay starts."""
    if not sys.stdin.isatty():
        mark_first_run_done(data_dir)
        return

    print()
    print(f"{_BOLD}┌──────────────────────────────────────┐{_RESET}")
    print(f"{_BOLD}│  Welcome to TokenPal!                │{_RESET}")
    print(f"{_BOLD}│  Your sarcastic AI desktop buddy.    │{_RESET}")
    print(f"{_BOLD}└──────────────────────────────────────┘{_RESET}")
    print()
    print("  Let's get you set up. This only takes a moment.")
    print()

    _setup_weather()

    print()
    print(f"  {_BOLD}Useful commands:{_RESET}")
    print(f"    /help    {_DIM}— see all commands{_RESET}")
    print(f"    /voice   {_DIM}— train character voices (Bender, GLaDOS, etc.){_RESET}")
    print(f"    /mood    {_DIM}— check TokenPal's mood{_RESET}")
    print(f"    /status  {_DIM}— see active senses and model{_RESET}")
    print(f"    /model   {_DIM}— switch or download LLM models{_RESET}")
    print()

    mark_first_run_done(data_dir)
    print(f"  {_GREEN}Ready!{_RESET} TokenPal is now watching. Type anything to chat.")
    print()

    try:
        input(f"  {_DIM}Press Enter to start...{_RESET}")
    except (EOFError, KeyboardInterrupt):
        pass


def _setup_weather() -> None:
    """Prompt for a zip code and configure weather sense."""
    print(f"  {_BOLD}1. Weather{_RESET}")
    print("     Enter a US zip code for weather-aware commentary.")
    print(f"     {_DIM}(Leave blank to skip — you can always use /zip later){_RESET}")

    try:
        zipcode = input("     Zip code: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not zipcode:
        return

    if not re.match(r"^\d{5}$", zipcode):
        print(f"     {_DIM}Doesn't look like a zip code — skipping. Use /zip later.{_RESET}")
        return

    try:
        from tokenpal.config.weather import geocode_zip, write_weather_config

        geo = geocode_zip(zipcode)
        if not geo:
            print(f"     {_DIM}No location found for {zipcode} — skipping. Use /zip later.{_RESET}")
            return

        write_weather_config(geo.lat, geo.lon, geo.label)
        print(f"     {_GREEN}Weather set to {geo.label}.{_RESET}")
    except Exception:
        print(f"     {_DIM}Couldn't configure weather — use /zip {zipcode} after startup.{_RESET}")
