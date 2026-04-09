"""CLI argument parsing and health-check command for TokenPal."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

import httpx

from tokenpal.config.loader import load_config

# Terminal colors (no dependency needed)
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"
_CHECK = f"{_GREEN}\u2713{_RESET}"
_WARN = f"{_YELLOW}!{_RESET}"
_FAIL = f"{_RED}\u2717{_RESET}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="tokenpal",
        description="TokenPal — your sarcastic AI desktop buddy",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="print version and exit",
    )
    parser.add_argument(
        "--check", "-c", action="store_true",
        help="verify Ollama, model, senses, and actions, then exit",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="show debug logs in terminal",
    )
    parser.add_argument(
        "--config", type=Path, default=None, metavar="PATH",
        help="path to config.toml",
    )
    return parser.parse_args(argv)


def print_version() -> None:
    """Print version and exit."""
    try:
        ver = pkg_version("tokenpal")
    except PackageNotFoundError:
        ver = "dev"
    print(f"tokenpal {ver}")


def run_check(config_path: Path | None = None) -> int:
    """Run health checks and print a status report. Returns 0 if all good, 1 if problems."""
    return asyncio.run(_check(config_path))


async def _check(config_path: Path | None) -> int:
    print(f"\n{_BOLD}TokenPal Health Check{_RESET}")
    print("-" * 40)
    problems = 0

    # Config
    config = load_config(config_path=config_path)
    print(f"  {_CHECK} Config loaded")

    # Ollama connectivity
    api_url = config.llm.api_url
    model_name = config.llm.model_name
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{api_url}/models")
            resp.raise_for_status()
            print(f"  {_CHECK} Ollama reachable at {api_url}")

            models = resp.json().get("data", [])
            model_ids = {m.get("id", "") for m in models}
            if model_name in model_ids:
                print(f"  {_CHECK} Model '{model_name}' available")
            else:
                print(f"  {_WARN} Model '{model_name}' not found")
                print(f"      Run: ollama pull {model_name}")
                problems += 1
    except httpx.HTTPError:
        print(f"  {_FAIL} Cannot reach Ollama at {api_url}")
        print("      Start it with: ollama serve")
        problems += 1

    # Senses
    from tokenpal.senses.registry import discover_senses, resolve_senses

    discover_senses(extra_packages=config.plugins.extra_packages)
    sense_flags = {
        f.name: getattr(config.senses, f.name)
        for f in dataclasses.fields(config.senses)
    }
    senses = resolve_senses(sense_flags=sense_flags, sense_overrides=config.plugins.sense_overrides)
    names = [s.sense_name for s in senses]
    print(f"  {_CHECK} {len(senses)} senses: {', '.join(names)}")

    # Actions
    from tokenpal.actions.registry import discover_actions, resolve_actions

    discover_actions()
    action_flags = {
        f.name: getattr(config.actions, f.name)
        for f in dataclasses.fields(config.actions)
        if f.name != "enabled"
    }
    actions = resolve_actions(enabled=action_flags) if config.actions.enabled else []
    if actions:
        anames = [a.action_name for a in actions]
        print(f"  {_CHECK} {len(actions)} actions: {', '.join(anames)}")
    else:
        print(f"  {_WARN} No actions enabled")

    # Summary
    print()
    if problems == 0:
        print(f"{_GREEN}{_BOLD}All checks passed.{_RESET}")
    else:
        print(f"{_YELLOW}{_BOLD}{problems} issue(s) found.{_RESET}")
    print()
    return 1 if problems else 0
