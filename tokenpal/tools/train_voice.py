"""CLI tool to extract character voice profiles from transcripts.

Usage:
    python -m tokenpal.tools.train_voice transcript.txt "Character Name"
    python -m tokenpal.tools.train_voice --wiki regularshow "Mordecai"
    python -m tokenpal.tools.train_voice quotes.txt --lines-only
    python -m tokenpal.tools.train_voice --list
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

from tokenpal.tools.transcript_parser import extract_lines, extract_lines_from_text
from tokenpal.tools.voice_profile import (
    list_profiles,
    make_profile,
    save_profile,
    _slugify,
)

_VOICES_DIR = Path.home() / ".tokenpal" / "voices"
_OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
_MODEL = "gemma3:4b"


def _ollama_generate(prompt: str, max_tokens: int = 60, temperature: float = 0.7) -> str | None:
    """Send a prompt to Ollama and return the response text."""
    payload = json.dumps({
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    req = urllib.request.Request(
        _OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            return text.strip("\"'").strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  Warning: Ollama call failed ({e})", file=sys.stderr)
        return None


def _generate_persona(character: str, lines: list[str]) -> str | None:
    """Ask Ollama to describe the character's voice from sample lines."""
    samples = random.sample(lines, min(15, len(lines)))
    samples_block = "\n".join(f"- {line}" for line in samples)

    return _ollama_generate(f"""Here are sample dialogue lines from the character "{character}":

{samples_block}

Based on these lines, write a ONE sentence persona description for an AI that should speak in this character's voice. Cover their tone, slang, and attitude. Max 30 words.

Write ONLY the persona description, nothing else. No quotes. One sentence.""")


def _generate_greetings(character: str, lines: list[str]) -> list[str]:
    """Ask Ollama to generate startup greetings in the character's voice."""
    samples = random.sample(lines, min(10, len(lines)))
    samples_block = "\n".join(f"- {line}" for line in samples)

    text = _ollama_generate(
        f"""Here are sample dialogue lines from "{character}":

{samples_block}

Write 10 short startup greetings (what this character would say when waking up or arriving). One per line, numbered 1-10. Each under 50 characters. Match their slang and attitude. No stage directions.""",
        max_tokens=200,
        temperature=0.9,
    )

    if not text:
        return []

    greetings: list[str] = []
    for line in text.splitlines():
        # Strip numbering like "1." or "1)"
        cleaned = re.sub(r"^\d+[.)]\s*", "", line).strip().strip("\"'").strip()
        if cleaned and 8 <= len(cleaned) <= 60:
            greetings.append(cleaned)

    return greetings


def _generate_offline_quips(character: str, lines: list[str]) -> list[str]:
    """Ask Ollama to generate confused/offline quips in the character's voice."""
    samples = random.sample(lines, min(10, len(lines)))
    samples_block = "\n".join(f"- {line}" for line in samples)

    text = _ollama_generate(
        f"""Here are sample dialogue lines from "{character}":

{samples_block}

Write 10 short confused/disoriented lines — what this character would say if they suddenly lost their train of thought or their brain stopped working. One per line, numbered 1-10. Each under 50 characters. Match their slang and attitude.""",
        max_tokens=200,
        temperature=0.9,
    )

    if not text:
        return []

    quips: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\d+[.)]\s*", "", line).strip().strip("\"'").strip()
        if cleaned and 8 <= len(cleaned) <= 60:
            quips.append(cleaned)

    return quips


def _find_config_toml() -> Path:
    """Find config.toml — check CWD first, then fall back to project root."""
    cwd = Path.cwd()
    if (cwd / "config.toml").exists():
        return cwd / "config.toml"
    if (cwd / "config.default.toml").exists():
        # We're in the project root, config.toml goes here
        return cwd / "config.toml"
    # Default: CWD
    return cwd / "config.toml"


def _activate_voice(slug: str) -> None:
    """Set active_voice in config.toml, creating the file/section if needed."""
    config_path = _find_config_toml()
    new_line = f'active_voice = "{slug}"'

    if config_path.exists():
        content = config_path.read_text()

        # Replace existing active_voice line
        if re.search(r'^active_voice\s*=', content, re.MULTILINE):
            content = re.sub(
                r'^active_voice\s*=.*$',
                new_line,
                content,
                flags=re.MULTILINE,
            )
        elif "[brain]" in content:
            # Add under existing [brain] section
            content = content.replace("[brain]", f"[brain]\n{new_line}", 1)
        else:
            # Append a new [brain] section
            content = content.rstrip() + f"\n\n[brain]\n{new_line}\n"
    else:
        content = f"[brain]\n{new_line}\n"

    config_path.write_text(content)


def _print_samples(lines: list[str], n: int = 5) -> None:
    """Print random samples for the user to eyeball."""
    samples = random.sample(lines, min(n, len(lines)))
    print("\nSample lines:")
    for line in samples:
        print(f'  "{line}"')
    print()


def _cmd_list() -> None:
    """List all saved voice profiles."""
    profiles = list_profiles(_VOICES_DIR)
    if not profiles:
        print("No voices saved yet.")
        print(f"Voices directory: {_VOICES_DIR}")
        return
    print(f"{'Slug':<25} {'Character':<25} {'Lines':>5}")
    print("-" * 57)
    for slug, character, count in profiles:
        print(f"{slug:<25} {character:<25} {count:>5}")


def _cmd_extract(args: argparse.Namespace) -> None:
    """Extract lines and optionally save a voice profile."""
    character = args.character

    if args.wiki:
        # Fetch from Fandom wiki
        if not character:
            print("Error: character name required with --wiki", file=sys.stderr)
            sys.exit(1)

        from tokenpal.tools.wiki_fetch import fetch_all_transcripts

        step_label = f"Fetching transcripts from {args.wiki}.fandom.com"
        print(f"\n{step_label}...")
        text = fetch_all_transcripts(args.wiki, max_pages=args.max_pages)
        if not text:
            print("Error: no transcripts found. Check the wiki name.", file=sys.stderr)
            print(f"  Try: https://{args.wiki}.fandom.com/wiki/Category:Transcripts", file=sys.stderr)
            sys.exit(1)

        lines = extract_lines_from_text(text, character=character)
        source = f"{args.wiki}.fandom.com"
    else:
        # Extract from local file
        if not args.file:
            print("Error: file is required (or use --wiki)", file=sys.stderr)
            sys.exit(1)

        path = Path(args.file)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

        if not character and not args.lines_only:
            print("Error: character name required (or use --lines-only)", file=sys.stderr)
            sys.exit(1)

        lines = extract_lines(path, character=character, lines_only=args.lines_only)
        source = path.name

    if not lines:
        label = f" for {character.upper()}" if character else ""
        print(f"No lines found{label}.", file=sys.stderr)
        print("Check the character name spelling.", file=sys.stderr)
        sys.exit(1)

    label = character.upper() if character else "input"
    print(f"Found {len(lines)} lines from {label}")
    _print_samples(lines)

    if args.preview:
        print("(preview mode — not saved)")
        return

    if len(lines) < args.min_lines:
        print(
            f"Only {len(lines)} lines found (minimum: {args.min_lines}). "
            f"Voice may be repetitive. Use --min-lines {len(lines)} to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Generate persona + greetings via Ollama
    name = character or path.stem
    persona = ""
    greetings: list[str] = []
    offline_quips: list[str] = []
    if not args.no_persona:
        print("Generating persona via Ollama...", end=" ", flush=True)
        persona = _generate_persona(name, lines) or ""
        if persona:
            print("done!")
            print(f"\nPersona: {persona}\n")
        else:
            print("skipped (Ollama unavailable)")

        print("Generating startup greetings...", end=" ", flush=True)
        greetings = _generate_greetings(name, lines)
        if greetings:
            print(f"done! ({len(greetings)} greetings)")
            for g in greetings[:5]:
                print(f'  "{g}"')
            print()
        else:
            print("skipped")

        print("Generating offline quips...", end=" ", flush=True)
        offline_quips = _generate_offline_quips(name, lines)
        if offline_quips:
            print(f"done! ({len(offline_quips)} quips)")
            for q in offline_quips[:5]:
                print(f'  "{q}"')
            print()
        else:
            print("skipped")

    profile = make_profile(
        character=name,
        source=source,
        lines=lines,
        persona=persona,
        greetings=greetings,
        offline_quips=offline_quips,
    )
    out_path = save_profile(profile, _VOICES_DIR)
    slug = _slugify(name)

    print(f"Saved voice \"{slug}\" ({len(lines)} lines) to {out_path}")

    # Auto-activate in config.toml
    _activate_voice(slug)
    print(f"Activated voice \"{slug}\" in config.toml — restart TokenPal to use it.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="train_voice",
        description="Extract character voice profiles from transcripts.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all saved voice profiles",
    )
    parser.add_argument(
        "file", nargs="?",
        help="Path to transcript or lines file",
    )
    parser.add_argument(
        "character", nargs="?",
        help="Character name to extract",
    )
    parser.add_argument(
        "--lines-only", action="store_true",
        help="Treat input as one quote per line (skip character extraction)",
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="Show extracted lines without saving",
    )
    parser.add_argument(
        "--min-lines", type=int, default=10,
        help="Minimum lines required to save (default: 10)",
    )
    parser.add_argument(
        "--no-persona", action="store_true",
        help="Skip persona generation via Ollama",
    )
    parser.add_argument(
        "--wiki", type=str, default="",
        help="Fetch transcripts from a Fandom wiki (e.g. 'regularshow', 'adventuretime')",
    )
    parser.add_argument(
        "--max-pages", type=int, default=500,
        help="Max transcript pages to fetch from wiki (default: 500)",
    )

    args = parser.parse_args()

    if args.list:
        _cmd_list()
        return

    if not args.file and not args.wiki:
        parser.error("file or --wiki is required (or use --list)")

    # When using --wiki, the first positional is the character, not a file
    if args.wiki and args.file and not args.character:
        args.character = args.file
        args.file = None

    _cmd_extract(args)


if __name__ == "__main__":
    main()
