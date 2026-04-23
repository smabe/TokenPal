"""CLI argument parsing and health-check command for TokenPal."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import platform
import shutil
import sys
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
        "--validate", action="store_true",
        help="comprehensive preflight check (superset of --check), then exit",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="show debug logs in terminal",
    )
    parser.add_argument(
        "--config", type=Path, default=None, metavar="PATH",
        help="path to config.toml",
    )
    parser.add_argument(
        "--skip-welcome", action="store_true",
        help="skip the first-run welcome wizard",
    )
    parser.add_argument(
        "--overlay", choices=("auto", "qt", "textual", "console", "tkinter"),
        default=None, metavar="NAME",
        help=(
            "override [ui] overlay from config. qt=desktop window, "
            "textual=rich TUI in terminal, console=ANSI-only terminal"
        ),
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


async def _check_inference(config: object) -> int:
    """Check inference-engine connectivity and model availability. Returns problem count."""
    problems = 0
    api_url = config.llm.api_url
    model_name = config.llm.model_name
    engine = getattr(config.llm, "inference_engine", "ollama")
    label = "llama-server" if engine == "llamacpp" else "Ollama"
    hint = "start-llamaserver.bat" if engine == "llamacpp" else "ollama serve"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{api_url}/models")
            resp.raise_for_status()
            print(f"  {_CHECK} {label} reachable at {api_url}")

            models = resp.json().get("data", [])
            model_ids = {m.get("id", "") for m in models}
            if engine == "llamacpp" or model_name in model_ids:
                print(f"  {_CHECK} Model '{model_name}' available")
            else:
                print(f"  {_WARN} Model '{model_name}' not found")
                print(f"      Run: ollama pull {model_name}")
                problems += 1
    except httpx.HTTPError:
        print(f"  {_FAIL} Cannot reach {label} at {api_url}")
        print(f"      Start it with: {hint}")
        problems += 1
    return problems


def _check_senses(config: object) -> int:
    """Discover and check senses. Returns problem count."""
    from tokenpal.senses.registry import discover_senses, resolve_senses

    discover_senses(extra_packages=config.plugins.extra_packages)
    sense_flags = {
        f.name: getattr(config.senses, f.name)
        for f in dataclasses.fields(config.senses)
    }
    senses = resolve_senses(
        sense_flags=sense_flags,
        sense_overrides=config.plugins.sense_overrides,
    )
    names = [s.sense_name for s in senses]
    print(f"  {_CHECK} {len(senses)} senses: {', '.join(names)}")

    resolved_names = set(names)
    enabled_names = {name for name, on in sense_flags.items() if on}
    skipped = enabled_names - resolved_names
    problems = 0
    for name in sorted(skipped):
        print(f"  {_WARN} '{name}' enabled but no implementation for this platform")
        problems += 1
    return problems


def _check_actions(config: object) -> None:
    """Discover and check actions."""
    from tokenpal.actions.registry import discover_actions, resolve_actions

    discover_actions()
    action_flags = {
        f.name: getattr(config.actions, f.name)
        for f in dataclasses.fields(config.actions)
        if f.name != "enabled"
    }
    from tokenpal.config.schema import DEFAULT_TOOLS

    actions = (
        resolve_actions(
            enabled=action_flags,
            optin_allowlist=set(getattr(config.tools, "enabled_tools", []) or []),
            default_tools=set(DEFAULT_TOOLS),
        )
        if config.actions.enabled
        else []
    )
    if actions:
        anames = [a.action_name for a in actions]
        print(f"  {_CHECK} {len(actions)} actions: {', '.join(anames)}")
    else:
        print(f"  {_WARN} No actions enabled")


def _check_utility_wedges(config: object) -> None:
    """Report on the session handoff / intent / EOD / rage / git-nudge
    features. Informational only; none of these raise validation problems.
    See plans/shipped/buddy-utility-wedges.md.
    """
    summary = getattr(config, "session_summary", None)
    if summary and getattr(summary, "enabled", False):
        print(
            f"  {_CHECK} Session handoff on "
            f"(every {summary.interval_s}s, {summary.max_lookback_h}h lookback)"
        )
    else:
        print(f"  {_WARN} Session handoff off")

    intent = getattr(config, "intent", None)
    if intent:
        drift_min = int(getattr(intent, "drift_min_dwell_s", 0))
        print(
            f"  {_CHECK} /intent drift detection "
            f"({drift_min}s dwell, "
            f"{len(getattr(intent, 'distraction_apps', []))} distraction apps)"
        )

    print(f"  {_CHECK} EOD summary available via /summary")

    rage = getattr(config, "rage_detect", None)
    if rage and getattr(rage, "enabled", False):
        print(f"  {_CHECK} Rage detect on")
    else:
        print(f"  {_WARN} Rage detect off (opt-in)")

    git_nudge = getattr(config, "git_nudge", None)
    if git_nudge and getattr(git_nudge, "enabled", False):
        stale_h = getattr(git_nudge, "wip_stale_hours", 0)
        print(f"  {_CHECK} Proactive git nudge on (>{stale_h}h WIP)")
    else:
        print(f"  {_WARN} Proactive git nudge off")


def _check_cloud_llm(config: object) -> None:
    """Report the cloud-LLM opt-in state. Informational only."""
    from tokenpal.config.secrets import fingerprint, get_cloud_key

    cfg = getattr(config, "cloud_llm", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        key = get_cloud_key()
        stored = " (key stored)" if key else ""
        print(f"  {_WARN} Cloud LLM off (local synth for /research){stored}")
        return
    key = get_cloud_key()
    if not key:
        print(f"  {_WARN} Cloud LLM enabled but no key - run /cloud enable <key>")
        return
    model = getattr(cfg, "model", "?")
    from tokenpal.llm.cloud_backend import DEEP_MODE_MODELS
    flags: list[str] = []
    if getattr(cfg, "research_plan", False):
        flags.append("planner")
    if getattr(cfg, "research_deep", False):
        if model in DEEP_MODE_MODELS:
            flags.append("deep mode")
        else:
            flags.append("deep set (Sonnet+ needed)")
    elif getattr(cfg, "research_search", False):
        if model in DEEP_MODE_MODELS:
            flags.append("search mode")
        else:
            flags.append("search set (Sonnet+ needed)")
    flag_str = f", {', '.join(flags)}" if flags else ""
    print(
        f"  {_CHECK} Cloud LLM on ({model}{flag_str}, "
        f"key {fingerprint(key)}) for /research synth"
    )


def _print_summary(problems: int) -> None:
    """Print pass/fail summary."""
    print()
    if problems == 0:
        print(f"{_GREEN}{_BOLD}All checks passed.{_RESET}")
    else:
        print(f"{_YELLOW}{_BOLD}{problems} issue(s) found.{_RESET}")
    print()


async def _check(config_path: Path | None) -> int:
    print(f"\n{_BOLD}TokenPal Health Check{_RESET}")
    print("-" * 40)
    problems = 0

    config = load_config(config_path=config_path)
    print(f"  {_CHECK} Config loaded")

    problems += await _check_inference(config)
    problems += _check_senses(config)
    _check_actions(config)

    _print_summary(problems)
    return 1 if problems else 0


def run_validate(config_path: Path | None = None) -> int:
    """Run comprehensive preflight validation. Returns 0 if all good, 1 if problems."""
    return asyncio.run(_validate(config_path))


async def _validate(config_path: Path | None) -> int:
    print(f"\n{_BOLD}TokenPal Preflight Validation{_RESET}")
    print("=" * 40)
    problems = 0

    # 1. Python version
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 12):
        print(f"  {_CHECK} Python {major}.{minor}")
    else:
        print(f"  {_FAIL} Python {major}.{minor} — 3.12+ required")
        problems += 1

    # 2. Platform-specific dependencies
    plat = platform.system()
    if plat == "Darwin":
        for mod, pkg_hint in [("Quartz", "pip install tokenpal[macos]"),
                              ("Cocoa", "pip install tokenpal[macos]")]:
            try:
                __import__(mod)
                print(f"  {_CHECK} {mod} available")
            except ImportError:
                print(f"  {_WARN} {mod} not found — {pkg_hint}")
                problems += 1
    elif plat == "Windows":
        try:
            __import__("win32gui")
            print(f"  {_CHECK} win32gui available")
        except ImportError:
            print(f"  {_WARN} win32gui not found — pip install tokenpal[windows]")
            problems += 1
    elif plat == "Linux":
        print(f"  {_WARN} Linux: app_awareness and music senses are unavailable")

    # 3. git binary
    if shutil.which("git"):
        print(f"  {_CHECK} git found in PATH")
    else:
        print(f"  {_WARN} git not found — git sense will not work")
        problems += 1

    # 4. Inference engine + model
    config = load_config(config_path=config_path)
    problems += await _check_inference(config)

    # 5. Config
    print(f"  {_CHECK} Config loaded")

    # 6. Senses + actions
    problems += _check_senses(config)
    _check_actions(config)

    # 6b. Utility wedges (session handoff, intent, EOD, rage, git nudge)
    _check_utility_wedges(config)

    # 6c. Cloud LLM (opt-in Anthropic synth for /research)
    _check_cloud_llm(config)

    # 7. macOS Accessibility reminder
    if plat == "Darwin":
        print()
        print(
            f"  {_WARN} macOS: ensure Accessibility permission is granted in"
            f" System Settings > Privacy & Security > Accessibility"
        )

    _print_summary(problems)
    return 1 if problems else 0
