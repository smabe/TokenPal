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
from collections.abc import Callable
from pathlib import Path

from tokenpal.tools.transcript_parser import extract_lines, extract_lines_from_text
from tokenpal.tools.voice_profile import (
    list_profiles,
    make_profile,
    save_profile,
    slugify,
)

_MODEL = "gemma3:4b"


def _get_voices_dir() -> Path:
    """Resolve voices directory from config, with fallback."""
    try:
        from tokenpal.config.loader import load_config
        config = load_config()
        return Path(config.paths.data_dir).expanduser().resolve() / "voices"
    except Exception:
        return Path.home() / ".tokenpal" / "voices"


def _get_ollama_url() -> str:
    """Resolve Ollama API URL from config, with fallback."""
    try:
        from tokenpal.config.loader import load_config
        config = load_config()
        return config.llm.api_url + "/chat/completions"
    except Exception:
        return "http://localhost:11434/v1/chat/completions"


def _ollama_generate(prompt: str, max_tokens: int = 60, temperature: float = 0.7) -> str | None:
    """Send a prompt to Ollama and return the response text."""
    payload = json.dumps({
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    req = urllib.request.Request(
        _get_ollama_url(),
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


def _generate_lines_from_prompt(character: str, lines: list[str], task_prompt: str) -> list[str]:
    """Generate numbered one-liners via Ollama from a character's voice samples."""
    samples = random.sample(lines, min(10, len(lines)))
    samples_block = "\n".join(f"- {line}" for line in samples)

    text = _ollama_generate(
        f"""Here are sample dialogue lines from "{character}":

{samples_block}

{task_prompt}""",
        max_tokens=200,
        temperature=0.9,
    )

    if not text:
        return []

    result: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\d+[.)]\s*", "", line).strip().strip("\"'").strip()
        if cleaned and 8 <= len(cleaned) <= 60:
            result.append(cleaned)
    return result


def _generate_greetings(character: str, lines: list[str]) -> list[str]:
    return _generate_lines_from_prompt(character, lines,
        "Write 10 short startup greetings (what this character would say when waking up "
        "or arriving). One per line, numbered 1-10. Each under 50 characters. "
        "Match their slang and attitude. No stage directions.")


def _generate_offline_quips(character: str, lines: list[str]) -> list[str]:
    return _generate_lines_from_prompt(character, lines,
        "Write 10 short confused/disoriented lines — what this character would say if "
        "they suddenly lost their train of thought or their brain stopped working. "
        "One per line, numbered 1-10. Each under 50 characters. Match their slang and attitude.")


def _generate_structure_hints(character: str, lines: list[str]) -> list[str]:
    return _generate_lines_from_prompt(character, lines,
        "Write 10 short style directions for how this character would comment on "
        "what someone is doing on their computer. Each should start with 'Respond' "
        "and describe the tone/format. One per line, numbered 1-10. Each under 60 characters. "
        "Examples: 'Respond as a casual bro observation.', 'Respond with excited slang.'\n"
        "Match this character's personality.")


def _generate_voice_assets(
    character: str, lines: list[str],
) -> tuple[str, list[str], list[str], dict[str, str], list[str]]:
    """Run all voice generation tasks in parallel.

    Returns (persona, greetings, offline_quips, mood_prompts, structure_hints).
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_p = pool.submit(_generate_persona, character, lines)
        f_g = pool.submit(_generate_greetings, character, lines)
        f_q = pool.submit(_generate_offline_quips, character, lines)
        f_m = pool.submit(_generate_mood_prompts, character, lines)
        f_s = pool.submit(_generate_structure_hints, character, lines)

    return (
        f_p.result() or "",
        f_g.result(),
        f_q.result(),
        f_m.result(),
        f_s.result(),
    )


def _generate_mood_prompts(character: str, lines: list[str]) -> dict[str, str]:
    """Generate character-specific mood descriptions for all 6 moods."""
    samples = random.sample(lines, min(10, len(lines)))
    samples_block = "\n".join(f"- {line}" for line in samples)

    text = _ollama_generate(
        f"""Here are sample dialogue lines from "{character}":

{samples_block}

Write 6 mood descriptions for this character. Each should be ONE sentence describing how this character acts in that mood, using their slang and personality. Format as "MOOD: description".

SNARKY: (their default dry/sharp attitude)
IMPRESSED: (grudging respect, backhanded compliments)
BORED: (nothing interesting happening)
CONCERNED: (worried about the user)
HYPER: (excited, energetic)
SLEEPY: (tired, half-awake)

Write ONLY the 6 lines, nothing else.""",
        max_tokens=300,
        temperature=0.8,
    )

    if not text:
        return {}

    moods: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        for mood in ("SNARKY", "IMPRESSED", "BORED", "CONCERNED", "HYPER", "SLEEPY"):
            if line.upper().startswith(mood):
                desc = line[len(mood):].lstrip(":- ").strip()
                if desc:
                    moods[mood.lower()] = f"Your current mood: {mood}. {desc}"
                break
    return moods


def train_from_wiki(
    wiki: str,
    character: str,
    voices_dir: Path | None = None,
    min_lines: int = 10,
    progress_callback: Callable[[str], None] | None = None,
) -> VoiceProfile | None:
    """Fetch transcripts from a Fandom wiki, extract lines, generate persona.

    Returns the saved VoiceProfile, or None if not enough lines found.
    """
    from tokenpal.tools.transcript_parser import extract_lines_from_text
    from tokenpal.tools.wiki_fetch import fetch_all_transcripts

    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    _progress(f"Fetching {wiki} transcripts...")
    text = fetch_all_transcripts(wiki, progress=False)
    if not text:
        return None

    _progress(f"Extracting {character}'s lines...")
    lines = extract_lines_from_text(text, character)
    if len(lines) < min_lines:
        return None

    _progress(f"Found {len(lines)} lines. Generating voice...")
    persona, greetings, offline_quips, mood_prompts, structure_hints = (
        _generate_voice_assets(character, lines)
    )

    _progress("Saving profile...")
    profile = make_profile(
        character=character,
        source=f"{wiki}.fandom.com",
        lines=lines,
        persona=persona,
        greetings=greetings,
        offline_quips=offline_quips,
        mood_prompts=mood_prompts,
        structure_hints=structure_hints,
    )

    out_dir = voices_dir or _get_voices_dir()
    save_profile(profile, out_dir)
    return profile


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
    profiles = list_profiles(_get_voices_dir())
    if not profiles:
        print("No voices saved yet.")
        print(f"Voices directory: {_get_voices_dir()}")
        return
    print(f"{'Slug':<25} {'Character':<25} {'Lines':>5}")
    print("-" * 57)
    for slug, character, count in profiles:
        print(f"{slug:<25} {character:<25} {count:>5}")


def _cmd_activate() -> None:
    """Interactive voice switcher — pick from saved profiles."""
    profiles = list_profiles(_get_voices_dir())
    if not profiles:
        print("No voices saved yet. Train one first:")
        print('  python -m tokenpal.tools.train_voice --wiki regularshow "Mordecai"')
        return

    print("Saved voices:\n")
    for i, (slug, character, count) in enumerate(profiles, 1):
        print(f"  {i}) {character} ({count} lines)")
    print(f"  0) Default TokenPal (no voice)")

    try:
        choice = input(f"\nSelect voice [0-{len(profiles)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not choice.isdigit():
        print("Cancelled.")
        return

    idx = int(choice)
    if idx == 0:
        _activate_voice("")
        print("Switched to default TokenPal voice. Restart to apply.")
    elif 1 <= idx <= len(profiles):
        slug, character, count = profiles[idx - 1]
        _activate_voice(slug)
        print(f"Switched to {character} ({count} lines). Restart TokenPal to apply.")
    else:
        print("Invalid choice.")


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
    mood_prompts: dict[str, str] = {}
    structure_hints: list[str] = []
    if not args.no_persona:
        print("Generating voice assets via Ollama...", flush=True)
        persona, greetings, offline_quips, mood_prompts, structure_hints = (
            _generate_voice_assets(name, lines)
        )

        if persona:
            print(f"\nPersona: {persona}")
        if greetings:
            print(f"\nStartup greetings ({len(greetings)}):")
            for g in greetings[:5]:
                print(f'  "{g}"')
        if offline_quips:
            print(f"\nOffline quips ({len(offline_quips)}):")
            for q in offline_quips[:5]:
                print(f'  "{q}"')
        if mood_prompts:
            print(f"\nMood prompts ({len(mood_prompts)}):")
            for mood, prompt in mood_prompts.items():
                print(f"  {mood}: {prompt}")
        if structure_hints:
            print(f"\nStyle hints ({len(structure_hints)}):")
            for h in structure_hints[:5]:
                print(f'  "{h}"')
        print()

    profile = make_profile(
        character=name,
        source=source,
        lines=lines,
        persona=persona,
        greetings=greetings,
        offline_quips=offline_quips,
        mood_prompts=mood_prompts,
        structure_hints=structure_hints,
    )
    out_path = save_profile(profile, _get_voices_dir())
    slug = slugify(name)

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
        "--activate", action="store_true",
        help="Switch between saved voice profiles",
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

    if args.activate:
        _cmd_activate()
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
