"""Voice profile storage — save/load/list character voice profiles."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# Fandom slug → display name (shared by training + runtime)
FANDOM_NAMES: dict[str, str] = {
    "adventuretime": "Adventure Time",
    "regularshow": "Regular Show",
}


def franchise_from_source(source: str) -> str:
    """Derive franchise display name from a fandom wiki source URL."""
    if not source:
        return ""
    slug = source.split(".")[0].split("/")[-1]
    return FANDOM_NAMES.get(slug, slug.title())


def parse_catchphrases(persona: str) -> list[str]:
    """Extract quoted catchphrases from a structured persona card."""
    for line in persona.splitlines():
        if line.strip().upper().startswith("CATCHPHRASES:"):
            text = line.split(":", 1)[1].strip()
            return re.findall(r'"([^"]+)"', text)
    return []


@dataclass
class VoiceProfile:
    character: str
    source: str
    created: str
    lines: list[str]
    persona: str = ""
    greetings: list[str] = field(default_factory=list)
    offline_quips: list[str] = field(default_factory=list)
    mood_prompts: dict[str, str] = field(default_factory=dict)
    mood_roles: dict[str, str] = field(default_factory=dict)
    default_mood: str = ""
    structure_hints: list[str] = field(default_factory=list)
    finetuned_model: str = ""
    finetuned_base: str = ""
    finetuned_date: str = ""
    anchor_lines: list[str] = field(default_factory=list)
    banned_names: list[str] = field(default_factory=list)
    version: int = 1

    @property
    def line_count(self) -> int:
        return len(self.lines)


def slugify(name: str) -> str:
    """Convert a character name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def save_profile(profile: VoiceProfile, voices_dir: Path) -> Path:
    """Save a voice profile to JSON. Returns the path written."""
    voices_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(profile.character)
    path = voices_dir / f"{slug}.json"
    data = asdict(profile)
    data["line_count"] = profile.line_count
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_profile(name: str, voices_dir: Path) -> VoiceProfile:
    """Load a voice profile by slug name. Raises FileNotFoundError if missing."""
    path = voices_dir / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return VoiceProfile(
        character=data["character"],
        source=data["source"],
        created=data["created"],
        lines=data["lines"],
        persona=data.get("persona", ""),
        greetings=data.get("greetings", []),
        offline_quips=data.get("offline_quips", []),
        mood_prompts=data.get("mood_prompts", {}),
        mood_roles=data.get("mood_roles", {}),
        default_mood=data.get("default_mood", ""),
        structure_hints=data.get("structure_hints", []),
        finetuned_model=data.get("finetuned_model", ""),
        finetuned_base=data.get("finetuned_base", ""),
        finetuned_date=data.get("finetuned_date", ""),
        anchor_lines=data.get("anchor_lines", []),
        banned_names=data.get("banned_names", []),
        version=data.get("version", 1),
    )


def list_profiles(voices_dir: Path) -> list[tuple[str, str, int]]:
    """List all saved profiles. Returns (slug, character_name, line_count) tuples."""
    if not voices_dir.exists():
        return []
    results = []
    for path in sorted(voices_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append((path.stem, data["character"], len(data["lines"])))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def make_profile(
    character: str,
    source: str,
    lines: list[str],
    persona: str = "",
    greetings: list[str] | None = None,
    offline_quips: list[str] | None = None,
    mood_prompts: dict[str, str] | None = None,
    mood_roles: dict[str, str] | None = None,
    default_mood: str = "",
    structure_hints: list[str] | None = None,
    anchor_lines: list[str] | None = None,
    banned_names: list[str] | None = None,
) -> VoiceProfile:
    """Create a new VoiceProfile with the current timestamp."""
    return VoiceProfile(
        character=character,
        source=source,
        created=datetime.now().isoformat(timespec="seconds"),
        lines=lines,
        persona=persona,
        greetings=greetings or [],
        offline_quips=offline_quips or [],
        mood_prompts=mood_prompts or {},
        mood_roles=mood_roles or {},
        default_mood=default_mood,
        structure_hints=structure_hints or [],
        anchor_lines=anchor_lines or [],
        banned_names=banned_names or [],
    )
