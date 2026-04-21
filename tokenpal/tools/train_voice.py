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
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.errors import MarkupError
from rich.text import Text

from tokenpal.config.toml_writer import update_config
from tokenpal.tools.transcript_parser import extract_lines, extract_lines_from_text
from tokenpal.tools.voice_profile import (
    FANDOM_NAMES,
    VoiceProfile,
    franchise_from_source,
    list_profiles,
    load_profile,
    make_profile,
    attach_visual_tells,
    parse_catchphrases,
    parse_visual_tells,
    save_profile,
    slugify,
)
from tokenpal.ui.ascii_skeletons import PALETTE_KEYS, SKELETONS
from tokenpal.ui.ascii_skeletons import render as _render_skeleton
from tokenpal.ui.ascii_zones import (
    BODY_MOTIF_RUBRIC,
    EYE_REGION_RUBRIC,
    FACIAL_HAIR_RUBRIC,
    HEADWEAR_RUBRIC,
    TRAILING_RUBRIC,
    normalize_zones,
    rubric_block,
)
from tokenpal.util.text_guards import is_clean_english


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


_ENGLISH_ONLY_SUFFIX = (
    "Write only in English. Plain text only, no markdown formatting, no analysis, "
    "no meta-commentary, no translations, no section headers."
)


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


def _validate_persona(text: str) -> bool:
    """Persona must be clean English and hit both required sections."""
    if not is_clean_english(text):
        return False
    upper = text.upper()
    return "VOICE:" in upper and "CATCHPHRASES:" in upper


def _generate_persona(
    character: str, lines: list[str], franchise: str = "",
) -> str | None:
    """Ask Ollama to generate a structured voice card from sample lines."""
    samples_block = _sample_block(lines, 25)
    origin = f' from {franchise}' if franchise else ''

    prompt = (
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
        "Be specific - name actual words or tones to avoid.\n"
        "WORLDVIEW: 1-2 sentences. What does this character care about? "
        "How do they see the world?\n\n"
        "Write ONLY the card. No preamble. Keep each section concise.\n\n"
        f"{_ENGLISH_ONLY_SUFFIX}"
    )
    for temp in (0.9, 0.7, 0.5):
        text = _ollama_generate(prompt, max_tokens=500, temperature=temp)
        if text and _validate_persona(text):
            return text
    return None


def _generate_visual_tells(character: str, franchise: str = "") -> str:
    """Ask the LLM to describe the character's canonical on-screen look.

    Separate call from the voice persona because appearance is pure
    recall of visual canon — not derivable from dialogue samples. Low
    temperature and a forceful "unknown if unsure" instruction keep
    hallucinated costumes out of the VISUAL field. Returns a one-line
    description or empty string if the model refuses / can't produce
    clean English.
    """
    origin = f" from {franchise}" if franchise else ""
    prompt = (
        f'Describe the on-screen appearance of "{character}"{origin}.\n\n'
        f"Recall how this character is actually drawn in the show. "
        f"List CANONICAL colors and the signature silhouette tells that "
        f"make them recognizable.\n\n"
        f"Format: ONE sentence. Include hair/hat color, skin color, "
        f"outfit colors, and 1-3 silhouette tells (hat shape, beard, "
        f"tail, antenna, eye count, etc.).\n\n"
        f"When applicable, surface these specific details so the silhouette "
        f"reads at 14-row ASCII resolution:\n"
        f"- If the character has a beard or mustache, describe its length "
        f"and shape (long rectangular, short stubble, thick mustache).\n"
        f"- If the character has unusual eyes (one giant eye, spiral/hypno "
        f"eyes, visor, solid black), call that out explicitly.\n"
        f"- If the character has a distinctive chest element (screen, door, "
        f"emblem, big single button), mention it.\n"
        f"- If the character has a tail or trailing element (curly tail, "
        f"drifting hair, floating wisps), describe its shape.\n"
        f"- If none of these apply, skip them. Do NOT invent a beard or "
        f"tail the character does not actually have.\n\n"
        f"Good example (Finn the Human): 'White bear-ear hood, pale "
        f"skin, cyan tee, dark navy shorts, bone-white skin visible "
        f"between shirt and shorts, green backpack strap.'\n\n"
        f"Good example (Bender): 'Shiny silver-gray cylindrical metal "
        f"body, single curved antenna on top, two small yellow eyes "
        f"close together, slot-grill mouth, chest door rectangle.'\n\n"
        f"STRICT RULES:\n"
        f"- Use ONLY the real canonical colors from the actual show.\n"
        f"- Do NOT invent or generalize. If you genuinely do not know "
        f"what this character looks like, respond with the single word "
        f"UNKNOWN instead of guessing.\n"
        f"- No preamble. One sentence only.\n\n"
        f"{_ENGLISH_ONLY_SUFFIX}"
    )
    # Factual recall, so cooler than the classifier's (0.5, 0.3) ladder.
    for temp in (0.3, 0.2):
        text = _ollama_generate(prompt, max_tokens=150, temperature=temp)
        if not text:
            continue
        line = text.strip().split("\n")[0].strip()
        if line.upper().startswith("UNKNOWN"):
            return ""
        if is_clean_english(line) and 20 <= len(line) <= 400:
            return line
    return ""


def _parse_numbered_lines(text: str) -> list[str]:
    """Strip numbering/quotes, keep lines that are 8-60 chars and clean English."""
    result: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\d+[.)]\s*", "", line).strip().strip("\"'").strip()
        if not (cleaned and 8 <= len(cleaned) <= 60):
            continue
        if not is_clean_english(cleaned):
            continue
        result.append(cleaned)
    return result


def _generate_lines_from_prompt(
    character: str, lines: list[str], task_prompt: str, *, min_accepted: int = 3,
) -> list[str]:
    """Generate numbered one-liners via Ollama from a character's voice samples.

    Retries with lower temperature if output drifts (non-English, meta-commentary,
    or fewer than ``min_accepted`` usable lines survive filtering).
    """
    samples_block = _sample_block(lines)
    prompt = (
        f'Here are sample dialogue lines from "{character}":\n\n'
        f"{samples_block}\n\n"
        f"{task_prompt}\n\n"
        f"{_ENGLISH_ONLY_SUFFIX}"
    )

    best: list[str] = []
    for temp in (0.9, 0.7, 0.5):
        text = _ollama_generate(prompt, max_tokens=200, temperature=temp)
        if not text:
            continue
        parsed = _parse_numbered_lines(text)
        if len(parsed) > len(best):
            best = parsed
        if len(parsed) >= min_accepted:
            return parsed
    return best


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


_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _frames_look_usable(
    idle: list[str], idle_alt: list[str], talking: list[str],
) -> bool:
    """Reject blank, stunted, monotone, or markup-broken frame sets.

    Each frame must have ≥4 non-empty lines, ≥2 distinct hex colors, and
    every line must parse with ``Text.from_markup``. Skeleton-rendered
    frames always pass; this guards against legacy v1 profiles audited
    via ``audit_profile``.
    """
    for frame in (idle, idle_alt, talking):
        if sum(1 for line in frame if line.strip()) < 4:
            return False
        colors = {m.group(0).lower() for m in _HEX_COLOR_RE.finditer("\n".join(frame))}
        if len(colors) < 2:
            return False
        for line in frame:
            try:
                Text.from_markup(line)
            except MarkupError:
                return False
    return True


# --- Skeleton-based ASCII generation ---
# The LLM classifies the character + picks a palette; the actual art
# comes from hand-drawn skeleton templates in ascii_skeletons.py.

_TALKING_MOUTH: dict[str, str] = {
    "▽": "◇",
    "◇": "ᗣ",
    "◡": "ω",
    "⌣": "ω",
    "ω": "ᗣ",
    "ᗣ": "◇",
    "─": "◇",
}

# Safe fallback when LLM classification fails (network error, persistent
# bad JSON, etc.). Neutral grey humanoid.
_DEFAULT_CLASSIFICATION: dict = {
    "skeleton": "humanoid_tall",
    "palette": {
        "hair": "#888888",
        "skin": "#f4d4a8",
        "outfit": "#4080bf",
        "accent": "#ffd700",
        "shadow": "#2a4a6a",
        "highlight": "#ffffff",
    },
    "eye": "●",
    "mouth": "▽",
    "zones": {
        "headwear": "none", "facial_hair": "none",
        "body_motif": "none", "eye_region": "none", "trailing": "none",
    },
}


def _generate_ascii_art(
    character: str, persona: str, source: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Classify the character + pick a palette, then render 3 frames.

    Tries the cloud classifier first when ``cfg.cloud_llm.enabled`` and a
    key is stored (Haiku's canonical-character recall is materially
    better than Qwen3-14B's), falling back silently to the local path on
    any error.

    Returns (idle, idle_alt, talking). Falls back to a neutral placeholder
    if neither path can produce valid classification JSON.
    """
    classification = _classify_via_cloud(character, persona, source)
    if classification is None:
        classification = _classify_character_for_skeleton(
            character, persona, source,
        )
    if classification is None:
        classification = _DEFAULT_CLASSIFICATION
    return _render_skeleton_frames(classification)


def _classify_via_cloud(
    character: str, persona: str, source: str = "",
) -> dict | None:
    """Route classification through CloudBackend when the user opted in.

    Returns None when cloud is disabled, no key is stored, the SDK isn't
    installed, or the call errors — caller falls back to the local path.
    Schema enforcement via Anthropic's ``output_config.format`` makes
    parse-level retries unnecessary on the cloud path.
    """
    try:
        from tokenpal.config.loader import load_config
        from tokenpal.config.secrets import get_cloud_key
    except Exception:
        return None
    try:
        cfg = load_config()
    except Exception:
        return None
    if not cfg.cloud_llm.enabled or not cfg.cloud_llm.voice_classifier:
        return None
    key = get_cloud_key()
    if not key:
        return None
    try:
        from tokenpal.llm.cloud_backend import CloudBackend, CloudBackendError
    except Exception:
        return None
    try:
        backend = CloudBackend(
            api_key=key, model=cfg.cloud_llm.model,
            timeout_s=cfg.cloud_llm.timeout_s,
        )
    except (ValueError, CloudBackendError):
        return None

    prompt = _build_classifier_prompt(character, persona, source)
    try:
        resp = backend.synthesize(prompt, max_tokens=600)
    except CloudBackendError:
        return None
    return _parse_classification_json(resp.text or "")


def _build_classifier_prompt(
    character: str, persona: str, source: str = "",
) -> str:
    """Shared classifier prompt used by both local and cloud paths."""
    franchise = franchise_from_source(source) if source else ""
    origin = f' from {franchise}' if franchise else ""
    visual_tells = parse_visual_tells(persona)
    visual_block = (
        f'\nVisual canon (USE THESE COLORS AND SHAPES):\n{visual_tells}\n'
        if visual_tells else
        '\nPick bright, terminal-readable colors (mid-luminance hex, '
        'nothing near #000000) — dark backgrounds hide dark palettes.\n'
    )
    return (
        f'Pick an ASCII buddy template for "{character}"{origin}.\n'
        f'{visual_block}\n'
        f'Persona:\n{persona[:400]}\n\n'
        f'Templates (pick ONE):\n'
        f'- humanoid_tall: standard hero/adventurer (Finn, Mordecai)\n'
        f'- humanoid_stocky: short/wide build (Dexter, Muscle Man)\n'
        f'- robot_boxy: rectangular robot (BMO, Bender)\n'
        f'- creature_small: tiny round pet/chibi (Nibbler)\n'
        f'- mystical_cloaked: wizard/jester in hood or robe (Ice King)\n'
        f'- ghost_floating: hovering spirit with no legs\n'
        f'- animal_quadruped: 4-legged pet/creature (Jake dog form)\n'
        f'- winged: humanoid with wings flared behind shoulders\n\n'
        f'Pick 6 hex colors that match the character:\n'
        f'- hair: hair / hat / head-top color\n'
        f'- skin: face / skin tone\n'
        f'- outfit: primary clothing\n'
        f'- accent: secondary trim color (buttons, crowns, gems)\n'
        f'- shadow: a darker variant for shading\n'
        f'- highlight: a brighter variant of outfit/accent (for sheen, '
        f'crown gleam, wing tip). One shade up from outfit.\n\n'
        f'Pick one eye glyph: ● ○ ◉ ◎ ⊙ ◐ ◑\n'
        f'Pick one mouth glyph: ▽ ◇ ◡ ⌣ ω ᗣ\n\n'
        f'Pick ONE headwear zone. "none" is fronted — use it unless '
        f'the character truly has that accessory in canon:\n'
        + rubric_block(HEADWEAR_RUBRIC)
        + 'Pick ONE facial_hair zone. "none" is fronted — use it '
        + 'unless the character has a canonical beard on screen:\n'
        + rubric_block(FACIAL_HAIR_RUBRIC)
        + 'Pick ONE body_motif zone. "none" is fronted — almost every '
        + 'character picks this unless they have an iconic chest '
        + 'element:\n'
        + rubric_block(BODY_MOTIF_RUBRIC)
        + 'Pick ONE eye_region zone. "none" is fronted — almost every '
        + 'character picks this. Only override for characters with '
        + 'truly unusual eye treatments on screen:\n'
        + rubric_block(EYE_REGION_RUBRIC)
        + 'Pick ONE trailing zone. "none" is fronted — only override for '
        + 'characters with a distinctive trailing element (tail, '
        + 'drifting hair):\n'
        + rubric_block(TRAILING_RUBRIC)
        + 'Output ONLY this JSON, no prose:\n'
        + '{"skeleton":"...","palette":{"hair":"#rrggbb",'
        + '"skin":"#rrggbb","outfit":"#rrggbb","accent":"#rrggbb",'
        + '"shadow":"#rrggbb","highlight":"#rrggbb"},'
        + '"eye":"...","mouth":"...",'
        + '"zones":{"headwear":"none","facial_hair":"none",'
        + '"body_motif":"none","eye_region":"none","trailing":"none"}}'
    )


def _classify_character_for_skeleton(
    character: str, persona: str, source: str = "",
) -> dict | None:
    """Ask the local LLM for a skeleton + palette + face glyphs as JSON.

    Retries once at a lower temperature. Returns None on persistent
    failure so callers can fall back to a default.
    """
    prompt = _build_classifier_prompt(character, persona, source)
    for temp in (0.5, 0.3):
        text = _ollama_generate(prompt, max_tokens=600, temperature=temp)
        parsed = _parse_classification_json(text or "")
        if parsed is not None:
            return parsed
    return None


def _parse_classification_json(text: str) -> dict | None:
    """Extract + validate a classification JSON blob from LLM output.

    Tolerates surrounding prose. Returns None if the blob is missing,
    malformed, uses an unknown skeleton, has a non-hex palette entry,
    or has multi-character eye/mouth glyphs. ``highlight`` is optional
    for legacy prompts; it falls back to the outfit color. ``zones`` is
    optional; illegal picks get normalized to ``"none"`` silently.
    """
    if not text:
        return None

    # Strip ``` fences the model likes to add.
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    skeleton = data.get("skeleton")
    if skeleton not in SKELETONS:
        return None

    palette = data.get("palette")
    if not isinstance(palette, dict):
        return None
    for key in ("hair", "skin", "outfit", "accent", "shadow"):
        value = palette.get(key)
        if not isinstance(value, str) or not _HEX_COLOR_RE.fullmatch(value):
            return None
    highlight = palette.get("highlight")
    if not isinstance(highlight, str) or not _HEX_COLOR_RE.fullmatch(highlight):
        highlight = palette["outfit"]

    for glyph_key in ("eye", "mouth"):
        glyph = data.get(glyph_key)
        # Single-codepoint string; a "[" open-bracket would sneak markup in.
        if not isinstance(glyph, str) or len(glyph) != 1 or glyph == "[":
            return None

    raw_zones = data.get("zones") if isinstance(data.get("zones"), dict) else {}
    zones = normalize_zones(skeleton, {k: str(v) for k, v in raw_zones.items()})

    return {
        "skeleton": skeleton,
        "palette": {
            "hair": palette["hair"],
            "skin": palette["skin"],
            "outfit": palette["outfit"],
            "accent": palette["accent"],
            "shadow": palette["shadow"],
            "highlight": highlight,
        },
        "eye": data["eye"],
        "mouth": data["mouth"],
        "zones": zones,
    }


def _render_skeleton_frames(
    classification: dict,
) -> tuple[list[str], list[str], list[str]]:
    """Render idle / idle_alt / talking from a validated classification."""
    skeleton = classification["skeleton"]
    base: dict[str, str] = {
        k: f"[{classification['palette'][k]}]" for k in PALETTE_KEYS
    }
    base["eye"] = classification["eye"]
    base["mouth"] = classification["mouth"]
    talking_mouth = _TALKING_MOUTH.get(classification["mouth"], "◇")
    zones = classification.get("zones") or {}

    def frame(**overrides: str) -> list[str]:
        return _render_skeleton(skeleton, base | overrides, zones)

    return frame(), frame(eye="─"), frame(mouth=talking_mouth)


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

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_p = pool.submit(_generate_persona, character, lines, franchise)
        f_v = pool.submit(_generate_visual_tells, character, franchise)
        f_g = pool.submit(_generate_greetings, character, lines)
        f_q = pool.submit(_generate_offline_quips, character, lines)
        f_m = pool.submit(_generate_mood_prompts, character, lines)
        f_s = pool.submit(_generate_structure_hints, character, lines)

    mood_prompts, mood_roles, default_mood = f_m.result()
    persona = attach_visual_tells(f_p.result() or "", f_v.result())

    # Extract anchor lines using catchphrases from the persona
    catchphrases = parse_catchphrases(persona)
    anchor_lines = _extract_anchor_lines(lines, catchphrases)
    banned_names = _derive_banned_names(source, character)

    # Generate ASCII art (needs persona for character description; source
    # gives the classifier franchise context for canonical colors).
    ascii_idle, ascii_idle_alt, ascii_talking = _generate_ascii_art(
        character, persona, source,
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
        "Write ONLY the 6 lines, nothing else.\n\n"
        f"{_ENGLISH_ONLY_SUFFIX}",
        max_tokens=300,
        temperature=0.8,
    )
    if not text or not is_clean_english(text):
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

    for temp in (0.8, 0.65, 0.5):
        text = _ollama_generate(
            prompt + "\n\n" + _ENGLISH_ONLY_SUFFIX,
            max_tokens=400,
            temperature=temp,
        )
        if not text or not is_clean_english(text):
            continue
        parsed = _parse_custom_moods(text)
        if parsed:
            return parsed

    # Fallback to legacy (hardcoded mood names, character descriptions).
    # Validate legacy output too — empty dict means the whole thing drifted
    # and the caller should WARN rather than silently ship {}.
    legacy = _generate_mood_prompts_legacy(character, lines)
    if not legacy:
        print(
            f"  WARN: mood generation failed for {character}; "
            "run /voice regenerate <slug> after training to retry.",
            file=sys.stderr,
        )
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


def regenerate_voice_assets(
    profile: VoiceProfile,
    voices_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> VoiceProfile:
    """Re-run every LLM-backed generator for a profile in place.

    Refreshes persona, anchor_lines, banned_names, greetings, offline_quips,
    mood_prompts/roles/default_mood, structure_hints, and ASCII art.
    Preserves lines, finetune metadata, source, and created.
    """
    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    franchise = franchise_from_source(profile.source)
    _progress(f"Regenerating {profile.character}...")

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_p = pool.submit(
            _generate_persona, profile.character, profile.lines, franchise,
        )
        f_v = pool.submit(_generate_visual_tells, profile.character, franchise)
        f_g = pool.submit(_generate_greetings, profile.character, profile.lines)
        f_q = pool.submit(_generate_offline_quips, profile.character, profile.lines)
        f_m = pool.submit(_generate_mood_prompts, profile.character, profile.lines)
        f_s = pool.submit(_generate_structure_hints, profile.character, profile.lines)

    persona = attach_visual_tells(f_p.result() or "", f_v.result())
    profile.persona = persona
    profile.anchor_lines = _extract_anchor_lines(
        profile.lines, parse_catchphrases(persona),
    )
    profile.banned_names = _derive_banned_names(
        profile.source, profile.character,
    )
    profile.greetings = f_g.result()
    profile.offline_quips = f_q.result()
    mood_prompts, mood_roles, default_mood = f_m.result()
    profile.mood_prompts = mood_prompts
    profile.mood_roles = mood_roles
    profile.default_mood = default_mood
    profile.structure_hints = f_s.result()

    # ASCII art depends on persona, so it runs serially after the pool.
    _progress(f"Generating ASCII art for {profile.character}...")
    (
        profile.ascii_idle,
        profile.ascii_idle_alt,
        profile.ascii_talking,
    ) = _generate_ascii_art(profile.character, persona, profile.source)

    profile.version = 2

    out_dir = voices_dir or _get_voices_dir()
    save_profile(profile, out_dir)

    report = audit_profile(profile)
    if report.issues:
        _progress(
            f"Saved {profile.character} (v2) with {len(report.issues)} "
            "health warnings. Run --audit to inspect."
        )
    else:
        _progress(f"Saved {profile.character} (v2)")
    return profile


def regenerate_ascii_art(
    profile: VoiceProfile,
    voices_dir: Path | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> VoiceProfile:
    """Re-run just the ASCII art generator and save the profile.

    Useful when the existing persona/anchor/mood data is good but the art
    came out flat, mispigmented, or wrong-proportioned — no need to spend
    the ~60s full-regen bake on the five LLM-backed generators that also
    fire in `regenerate_voice_assets`.
    """
    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    _progress(f"Generating ASCII art for {profile.character}...")
    (
        profile.ascii_idle,
        profile.ascii_idle_alt,
        profile.ascii_talking,
    ) = _generate_ascii_art(
        profile.character, profile.persona, profile.source,
    )

    out_dir = voices_dir or _get_voices_dir()
    save_profile(profile, out_dir)
    _progress(f"Saved {profile.character} ASCII art")
    return profile


@dataclass
class AuditReport:
    slug: str
    character: str
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


def audit_profile(profile: VoiceProfile) -> AuditReport:
    """Inspect a profile for drift/empty-fallback damage. Returns a report."""
    issues: list[str] = []

    if not profile.persona.strip():
        issues.append("persona is empty")
    elif not _validate_persona(profile.persona):
        issues.append("persona failed English/format validation")

    for field_name, value in (
        ("greetings", profile.greetings),
        ("offline_quips", profile.offline_quips),
        ("structure_hints", profile.structure_hints),
    ):
        if not value:
            issues.append(f"{field_name} is empty")
            continue
        dirty = [v for v in value if not is_clean_english(v)]
        if dirty:
            issues.append(
                f"{field_name} contains {len(dirty)} non-English / meta entries"
            )

    if not profile.mood_prompts:
        issues.append("mood_prompts is empty")
    if not profile.mood_roles:
        issues.append("mood_roles is empty")
    if not profile.default_mood:
        issues.append("default_mood is empty")

    if not _frames_look_usable(
        profile.ascii_idle, profile.ascii_idle_alt, profile.ascii_talking,
    ):
        issues.append("ascii frames are blank or too short")

    return AuditReport(
        slug=slugify(profile.character),
        character=profile.character,
        issues=issues,
    )


def _cmd_audit(target: str | None) -> int:
    """Run the health audit. ``target`` is a slug or None to audit all."""
    voices_dir = _get_voices_dir()
    if not target:
        slugs = [slug for slug, _, _ in list_profiles(voices_dir)]
    else:
        slugs = [slugify(target)]

    if not slugs:
        print("No voice profiles found.")
        return 0

    any_broken = False
    for slug in slugs:
        try:
            profile = load_profile(slug, voices_dir)
        except FileNotFoundError:
            print(f"{slug}: NOT FOUND")
            any_broken = True
            continue
        report = audit_profile(profile)
        if report.ok:
            print(f"{slug:<20} OK  ({profile.character})")
        else:
            any_broken = True
            print(f"{slug:<20} BROKEN  ({profile.character}):")
            for issue in report.issues:
                print(f"    - {issue}")

    return 1 if any_broken else 0


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

    report = audit_profile(profile)
    if report.issues:
        print(f"\nWARN: {len(report.issues)} health issue(s):", file=sys.stderr)
        for issue in report.issues:
            print(f"  - {issue}", file=sys.stderr)
        print(
            f"  Re-run with /voice regenerate {slug} to retry the broken fields.",
            file=sys.stderr,
        )

    # Auto-activate in config.toml
    activate_voice(slug)
    print(f"Activated voice \"{slug}\" in config.toml. Restart TokenPal to use it.")


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
    parser.add_argument(
        "--audit", nargs="?", const="", default=None,
        help="Audit voice profiles for drift/empty-field damage. "
             "Pass a slug, or no argument to audit every profile.",
    )

    args = parser.parse_args()

    if args.audit is not None:
        sys.exit(_cmd_audit(args.audit))

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
