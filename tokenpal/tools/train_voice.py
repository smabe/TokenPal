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
from typing import Any

from tokenpal.config.toml_writer import update_config
from tokenpal.tools.transcript_parser import extract_lines, extract_lines_from_text
from tokenpal.tools.voice_profile import (
    FANDOM_NAMES,
    franchise_from_source,
    list_profiles,
    make_profile,
    parse_catchphrases,
    save_profile,
    slugify,
)

def _get_model() -> str:
    """Resolve model name from config, with fallback."""
    try:
        from tokenpal.config.loader import load_config
        config = load_config()
        return config.llm.model_name
    except Exception:
        return "gemma4"


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
        "model": _get_model(),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": "none",
    }).encode()

    req = urllib.request.Request(
        _get_ollama_url(),
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"].strip()
            return text.strip("\"'").strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  Warning: Ollama call failed ({e})", file=sys.stderr)
        return None


def _sample_block(lines: list[str], n: int = 10) -> str:
    """Pick up to *n* random lines and format as a bulleted block."""
    samples = random.sample(lines, min(n, len(lines)))
    return "\n".join(f"- {line}" for line in samples)


# Franchise → character names for cross-franchise banning
_FRANCHISE_CHARACTERS: dict[str, list[str]] = {
    "Adventure Time": [
        "Finn", "Jake", "BMO", "Marceline", "Princess Bubblegum",
        "Ice King", "Lumpy Space Princess", "Prismo",
    ],
    "Futurama": [
        "Bender", "Fry", "Leela", "Zoidberg", "Professor",
        "Hermes", "Amy", "Nibbler",
    ],
    "Regular Show": [
        "Mordecai", "Rigby", "Muscle Man", "Pops", "Benson",
        "Skips", "Hi Five Ghost", "Thomas",
    ],
}


def _derive_banned_names(
    source: str, character: str,
) -> list[str]:
    """Build list of character names from OTHER franchises."""
    franchise = franchise_from_source(source)
    banned: list[str] = []
    for fran, names in _FRANCHISE_CHARACTERS.items():
        if fran == franchise:
            continue
        banned.extend(names)
    # Also exclude own name from banned list (shouldn't ban self-reference)
    return [n for n in banned if n.lower() != character.lower()]


def _score_line(line: str, catchphrases_lower: list[str]) -> float:
    """Score a dialogue line for character distinctiveness."""
    score = 0.0
    # Length sweet spot: 20-80 chars
    if 20 <= len(line) <= 80:
        score += 0.3
    elif len(line) < 15:
        score -= 0.3
    # Contains a catchphrase (pre-lowercased)
    line_lower = line.lower()
    for phrase in catchphrases_lower:
        if phrase in line_lower:
            score += 0.5
            break
    # Has personality markers
    if "!" in line:
        score += 0.1
    # Penalize pure exclamations / sound effects
    stripped = line.strip("!?. ")
    if len(stripped) < 10:
        score -= 0.5
    return score


def _extract_anchor_lines(
    lines: list[str], catchphrases: list[str], max_anchors: int = 150,
) -> list[str]:
    """Score all lines and return the top N most distinctive."""
    catchphrases_lower = [p.lower() for p in catchphrases]
    scored = [(line, _score_line(line, catchphrases_lower)) for line in lines]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [line for line, _ in scored[:max_anchors]]


def _generate_persona(
    character: str, lines: list[str], franchise: str = "",
) -> str | None:
    """Ask Ollama to generate a structured voice card from sample lines."""
    samples_block = _sample_block(lines, 25)
    origin = f' from {franchise}' if franchise else ''

    return _ollama_generate(
        f'You are analyzing dialogue from "{character}"{origin}.\n\n'
        f"Here are 25 sample lines:\n{samples_block}\n\n"
        f"Write a character voice card for {character}. "
        "Use \"You [verb]\" instructions, not personality adjectives.\n"
        "Use this EXACT format:\n\n"
        "VOICE: How does this character talk? 2-3 \"You [verb] [pattern]\" "
        "instructions. Mention specific word choices, sentence structure, "
        "and energy level.\n"
        "CATCHPHRASES: 3-5 signature phrases in quotes, comma-separated. "
        "Only real ones from the dialogue.\n"
        "NEVER: 3-4 things this character would NEVER say or do. "
        "Be specific — name actual words or tones to avoid.\n"
        "WORLDVIEW: 1-2 sentences. What does this character care about? "
        "How do they see the world?\n\n"
        "Write ONLY the card. No preamble. Keep each section concise.",
        max_tokens=500,
        temperature=0.7,
    )


def _generate_lines_from_prompt(character: str, lines: list[str], task_prompt: str) -> list[str]:
    """Generate numbered one-liners via Ollama from a character's voice samples."""
    samples_block = _sample_block(lines)

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


def _generate_ascii_art(
    character: str, persona: str,
) -> tuple[list[str], list[str], list[str]]:
    """Generate 3 Rich-markup ASCII art frames for a character.

    Returns (idle, idle_alt, talking) as lists of markup lines.
    """
    text = _ollama_generate(
        f'You are a pixel artist who creates detailed terminal character art '
        f'using Unicode and Rich markup.\n\n'
        f'Create 3 frames of "{character}" for a terminal buddy app.\n\n'
        f'Character info:\n{persona[:300]}\n\n'
        f'REQUIREMENTS:\n'
        f'- Each frame EXACTLY 10 lines tall, 20-24 characters wide '
        f'(not counting markup tags)\n'
        f'- Use Rich markup: [#ff6600]text[/], [bold #00ccff]text[/]\n'
        f'- Colors MUST be hex codes like #ff6600. Do NOT use named '
        f'colors (silver, gray, red, etc) — they crash the renderer\n'
        f'- Use 2-3 colors that match the character (hair, outfit, etc)\n'
        f'- Build the FULL body: head, face, torso, arms, legs\n'
        f'- Use half-block chars ▄▀ for curves, █░▓▒ for fills, '
        f'│─┌┐└┘ for edges, ◆○● for eyes\n'
        f'- Make it DETAILED — fill the space, no empty rectangles\n'
        f'- Include the character\'s signature features (hat, hair, '
        f'weapon, outfit details)\n'
        f'- idle_alt: same as idle but eyes change (blink: ○→─)\n'
        f'- talking: mouth open or speech indicator\n\n'
        f'Here is an example of the level of detail expected:\n'
        f'[#00ccff]    ▄███▄[/]\n'
        f'[#00ccff]   █[/][#ffffff]○   ○[/][#00ccff]█[/]\n'
        f'[#00ccff]   █[/][#ffffff]  ▽  [/][#00ccff]█[/]\n'
        f'[#00ccff]    ▀███▀[/]\n'
        f'[#ff6600]   ╔═════╗[/]\n'
        f'[#ff6600]   ║  ◇  ║[/]\n'
        f'[#ff6600]   ╚══╦══╝[/]\n'
        f'[#ffffff]    ▄▀ ▀▄[/]\n\n'
        f'Output EXACTLY this format, nothing else:\n'
        f'IDLE:\n(10 lines of art)\n'
        f'IDLE_ALT:\n(10 lines of art)\n'
        f'TALKING:\n(10 lines of art)',
        max_tokens=1200,
        temperature=0.8,
    )

    if not text:
        return [], [], []

    return _parse_ascii_frames(text)


def _parse_ascii_frames(text: str) -> tuple[list[str], list[str], list[str]]:
    """Parse LLM output into three 8-line frames."""
    # Strip markdown fences
    text = re.sub(r"```\w*\n?", "", text)

    idle: list[str] = []
    idle_alt: list[str] = []
    talking: list[str] = []

    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper().rstrip(":")
        if upper == "IDLE":
            current = idle
        elif upper in ("IDLE_ALT", "IDLE ALT"):
            current = idle_alt
        elif upper == "TALKING":
            current = talking
        elif current is not None and len(current) < 10:
            current.append(line.rstrip())

    # Pad short frames to 10 lines
    for frame in (idle, idle_alt, talking):
        while len(frame) < 10:
            frame.append("")

    # If idle_alt is empty/identical, copy idle with a minor tweak
    if idle and not any(idle_alt):
        idle_alt[:] = list(idle)

    return idle, idle_alt, talking


def _generate_voice_assets(
    character: str,
    lines: list[str],
    source: str = "",
) -> tuple[
    str, list[str], list[str], dict[str, str], dict[str, str],
    str, list[str], list[str], list[str],
    list[str], list[str], list[str],
]:
    """Run all voice generation tasks in parallel.

    Returns (persona, greetings, offline_quips, mood_prompts, mood_roles,
             default_mood, structure_hints, anchor_lines, banned_names,
             ascii_idle, ascii_idle_alt, ascii_talking).
    """
    from concurrent.futures import ThreadPoolExecutor

    franchise = franchise_from_source(source)

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_p = pool.submit(_generate_persona, character, lines, franchise)
        f_g = pool.submit(_generate_greetings, character, lines)
        f_q = pool.submit(_generate_offline_quips, character, lines)
        f_m = pool.submit(_generate_mood_prompts, character, lines)
        f_s = pool.submit(_generate_structure_hints, character, lines)

    mood_prompts, mood_roles, default_mood = f_m.result()
    persona = f_p.result() or ""

    # Extract anchor lines using catchphrases from the persona
    catchphrases = parse_catchphrases(persona)
    anchor_lines = _extract_anchor_lines(lines, catchphrases)
    banned_names = _derive_banned_names(source, character)

    # Generate ASCII art (needs persona for character description)
    ascii_idle, ascii_idle_alt, ascii_talking = _generate_ascii_art(
        character, persona,
    )

    return (
        persona,
        f_g.result(),
        f_q.result(),
        mood_prompts,
        mood_roles,
        default_mood,
        f_s.result(),
        anchor_lines,
        banned_names,
        ascii_idle,
        ascii_idle_alt,
        ascii_talking,
    )


_MOOD_ROLES = ("DEFAULT", "SLEEPY", "BORED", "HYPER", "IMPRESSED", "CONCERNED")
_RE_MOOD_LINE = re.compile(
    r"^(" + "|".join(_MOOD_ROLES) + r")"
    r"\s*\|\s*"
    r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)"
    r"\s*\|\s*"
    r"(.+)$",
)


def _parse_custom_moods(
    text: str,
) -> tuple[dict[str, str], dict[str, str], str] | None:
    """Parse pipe-delimited mood output.

    Returns (mood_prompts, mood_roles, default_mood) or None on failure.
    mood_prompts: role-keyed prompt strings.
    mood_roles: role -> custom display name.
    """
    prompts: dict[str, str] = {}
    roles: dict[str, str] = {}
    for line in text.strip().splitlines():
        m = _RE_MOOD_LINE.match(line.strip())
        if not m:
            continue
        role = m.group(1).lower()
        name = m.group(2).upper()
        desc = m.group(3).strip()
        if desc[-1] not in ".!?":
            desc += "."
        prompts[role] = f"Your current mood: {name}. {desc}"
        roles[role] = name

    expected = {r.lower() for r in _MOOD_ROLES}
    if set(prompts.keys()) != expected:
        return None
    if len(set(roles.values())) != len(_MOOD_ROLES):
        return None  # duplicate names

    default_mood = roles.get("default", "")
    return prompts, roles, default_mood


def _generate_mood_prompts_legacy(
    character: str, lines: list[str],
) -> dict[str, str]:
    """Legacy fallback: hardcoded mood names with character descriptions."""
    samples_block = _sample_block(lines)

    text = _ollama_generate(
        f'Here are sample dialogue lines from "{character}":\n\n'
        f"{samples_block}\n\n"
        "Write 6 mood descriptions for this character. Each should be ONE "
        "sentence describing how this character acts in that mood, using their "
        'slang and personality. Format as "MOOD: description".\n\n'
        "SNARKY: (their default dry/sharp attitude)\n"
        "IMPRESSED: (grudging respect, backhanded compliments)\n"
        "BORED: (nothing interesting happening)\n"
        "CONCERNED: (worried about the user)\n"
        "HYPER: (excited, energetic)\n"
        "SLEEPY: (tired, half-awake)\n\n"
        "Write ONLY the 6 lines, nothing else.",
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


def _generate_mood_prompts(
    character: str, lines: list[str],
) -> tuple[dict[str, str], dict[str, str], str]:
    """Generate character-specific mood names and descriptions.

    Returns (mood_prompts, mood_roles, default_mood).
    """
    samples_block = _sample_block(lines)

    prompt = (
        f'Here are sample dialogue lines from "{character}":\n\n'
        f"{samples_block}\n\n"
        "This character needs 6 moods for an AI buddy app. Each mood has a "
        "ROLE (how the app triggers it) and a NAME (what the character would "
        "call that feeling).\n\n"
        "Pick a mood name that fits this character's personality for each role, "
        "then write a one-sentence mood description in their voice.\n\n"
        "Format each mood as exactly:\n"
        "ROLE | NAME | description\n\n"
        "The 6 roles (one line each, in this order):\n"
        "DEFAULT | <their neutral/signature attitude> | <description>\n"
        "SLEEPY | <their version of tired/drowsy> | <description>\n"
        "BORED | <their version of bored/restless> | <description>\n"
        "HYPER | <their version of excited/energetic> | <description>\n"
        "IMPRESSED | <their version of grudging respect> | <description>\n"
        "CONCERNED | <their version of worried/caring> | <description>\n\n"
        "Rules:\n"
        "- Mood names must be ONE WORD, all caps, no punctuation\n"
        "- Each description must be ONE sentence using the character's slang "
        "and speech patterns\n"
        "- Write ONLY the 6 lines, nothing else"
    )

    for _attempt in range(2):
        text = _ollama_generate(prompt, max_tokens=400, temperature=0.8)
        if not text:
            continue
        parsed = _parse_custom_moods(text)
        if parsed:
            return parsed

    # Fallback to legacy (hardcoded mood names, character descriptions)
    legacy = _generate_mood_prompts_legacy(character, lines)
    return legacy, {}, ""


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

    source = f"{wiki}.fandom.com"
    _progress(f"Found {len(lines)} lines. Generating voice...")
    (
        persona, greetings, offline_quips, mood_prompts,
        mood_roles, default_mood, structure_hints,
        anchor_lines, banned_names,
        ascii_idle, ascii_idle_alt, ascii_talking,
    ) = _generate_voice_assets(character, lines, source)

    _progress("Saving profile...")
    profile = make_profile(
        character=character,
        source=source,
        lines=lines,
        persona=persona,
        greetings=greetings,
        offline_quips=offline_quips,
        mood_prompts=mood_prompts,
        mood_roles=mood_roles,
        default_mood=default_mood,
        structure_hints=structure_hints,
        anchor_lines=anchor_lines,
        banned_names=banned_names,
    )
    profile.ascii_idle = ascii_idle
    profile.ascii_idle_alt = ascii_idle_alt
    profile.ascii_talking = ascii_talking

    out_dir = voices_dir or _get_voices_dir()
    save_profile(profile, out_dir)
    return profile


def regenerate_persona(
    profile: VoiceProfile,
    voices_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> VoiceProfile:
    """Re-generate only the persona for an existing profile.

    Updates persona, anchor_lines, banned_names, and bumps version.
    Preserves all other fields (greetings, moods, lines, etc.).
    """
    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    franchise = franchise_from_source(profile.source)
    _progress(f"Generating persona for {profile.character}...")
    persona = _generate_persona(
        profile.character, profile.lines, franchise,
    ) or ""

    catchphrases = parse_catchphrases(persona)
    anchor_lines = _extract_anchor_lines(profile.lines, catchphrases)
    banned_names = _derive_banned_names(
        profile.source, profile.character,
    )

    profile.persona = persona
    profile.anchor_lines = anchor_lines
    profile.banned_names = banned_names

    _progress(f"Generating ASCII art for {profile.character}...")
    ascii_idle, ascii_idle_alt, ascii_talking = _generate_ascii_art(
        profile.character, persona,
    )
    profile.ascii_idle = ascii_idle
    profile.ascii_idle_alt = ascii_idle_alt
    profile.ascii_talking = ascii_talking

    profile.version = 2

    out_dir = voices_dir or _get_voices_dir()
    save_profile(profile, out_dir)
    _progress(f"Saved {profile.character} (v2)")
    return profile


def activate_voice(slug: str) -> None:
    """Set active_voice in config.toml, creating the file/section if needed."""
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("brain", {})["active_voice"] = slug

    update_config(mutate)


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
        activate_voice("")
        print("Switched to default TokenPal voice. Restart to apply.")
    elif 1 <= idx <= len(profiles):
        slug, character, count = profiles[idx - 1]
        activate_voice(slug)
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
    mood_roles: dict[str, str] = {}
    default_mood: str = ""
    structure_hints: list[str] = []
    anchor_lines: list[str] = []
    banned_names: list[str] = []
    if not args.no_persona:
        print("Generating voice assets via Ollama...", flush=True)
        (
            persona, greetings, offline_quips, mood_prompts,
            mood_roles, default_mood, structure_hints,
            anchor_lines, banned_names,
        ) = _generate_voice_assets(name, lines, source)

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
            for role, prompt in mood_prompts.items():
                display = mood_roles.get(role, role.upper())
                print(f"  {display} ({role}): {prompt}")
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
        mood_roles=mood_roles,
        default_mood=default_mood,
        structure_hints=structure_hints,
        anchor_lines=anchor_lines,
        banned_names=banned_names,
    )
    out_path = save_profile(profile, _get_voices_dir())
    slug = slugify(name)

    print(f"Saved voice \"{slug}\" ({len(lines)} lines) to {out_path}")

    # Auto-activate in config.toml
    activate_voice(slug)
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
