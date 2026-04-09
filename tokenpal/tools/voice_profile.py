"""Voice profile storage — save/load/list character voice profiles."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class VoiceProfile:
    character: str
    source: str
    created: str
    lines: list[str]
    persona: str = ""
    greetings: list[str] = field(default_factory=list)
    offline_quips: list[str] = field(default_factory=list)
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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def load_profile(name: str, voices_dir: Path) -> VoiceProfile:
    """Load a voice profile by slug name. Raises FileNotFoundError if missing."""
    path = voices_dir / f"{name}.json"
    data = json.loads(path.read_text())
    return VoiceProfile(
        character=data["character"],
        source=data["source"],
        created=data["created"],
        lines=data["lines"],
        persona=data.get("persona", ""),
        greetings=data.get("greetings", []),
        offline_quips=data.get("offline_quips", []),
        version=data.get("version", 1),
    )


def list_profiles(voices_dir: Path) -> list[tuple[str, str, int]]:
    """List all saved profiles. Returns (slug, character_name, line_count) tuples."""
    if not voices_dir.exists():
        return []
    results = []
    for path in sorted(voices_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
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
    )
