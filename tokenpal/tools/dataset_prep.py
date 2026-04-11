"""Convert voice profile lines into ShareGPT-format JSONL for LoRA fine-tuning."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from tokenpal.tools.voice_profile import VoiceProfile, load_profile

# ---------------------------------------------------------------------------
# Synthetic context prompts — mirrors what TokenPal senses actually produce.
# Each entry is a realistic "What you see right now:" block.
# ---------------------------------------------------------------------------

_CONTEXT_POOL: list[str] = [
    # App awareness (most common sense reading)
    'App: VS Code, window title: "main.py"',
    'App: VS Code, window title: "index.ts"',
    'App: Google Chrome, window title: "GitHub - Pull Request #42"',
    'App: Google Chrome, window title: "Stack Overflow"',
    'App: Google Chrome, window title: "Reddit - r/programming"',
    "App: Terminal",
    "App: Slack",
    'App: Finder, window title: "Downloads"',
    "App: Safari",
    'App: Safari, window title: "YouTube"',
    "App: Notes",
    "App: Preview",
    'App: Discord, window title: "general"',
    "App: Spotify",
    "App: iTerm2",
    "App: Ghostty",
    # App + time combos
    (
        'App: Google Chrome, window title: "Reddit"\n'
        "It's 11:42 PM on Tuesday, user has been working for 3 hours"
    ),
    (
        'App: VS Code, window title: "server.py"\n'
        "It's 9:15 AM on Monday, user has been working for 12 minutes"
    ),
    (
        "App: Terminal\n"
        "It's 2:30 AM on Wednesday, user has been working for 5 hours"
    ),
    (
        "App: Slack\n"
        "It's 3:00 PM on Friday, user has been working for 6 hours"
    ),
    (
        'App: Google Chrome, window title: "YouTube"\n'
        "It's 1:15 PM on Thursday, user has been working for 45 minutes"
    ),
    # App + hardware
    "App: Terminal\nCPU 92% \u2014 working hard, RAM 67%",
    (
        'App: VS Code, window title: "build.sh"\n'
        "CPU 85% \u2014 working hard, RAM 78% \u2014 getting crowded"
    ),
    "App: Google Chrome\nCPU 12%, RAM 45%",
    "App: Spotify\nCPU 8%, RAM 52%",
    # App + idle returns
    "App: VS Code\nUser returned after a 5-minute break",
    "App: Google Chrome\nUser returned after 15 minutes away",
    (
        "App: Terminal\n"
        "User returned after being away for 1.2 hours"
    ),
    # Full context blocks (multiple senses)
    (
        'App: VS Code, window title: "test_auth.py"\n'
        "It's 10:30 AM on Monday, user has been working for 1 hour\n"
        "CPU 15%, RAM 42%"
    ),
    (
        'App: Google Chrome, window title: "Netflix"\n'
        "It's 11:00 PM on Saturday, "
        "user has been working for 20 minutes\n"
        "CPU 5%, RAM 38%"
    ),
    (
        "App: Terminal\n"
        "It's 3:33 AM on Sunday, user has been working for 7 hours\n"
        "CPU 45%, RAM 61%"
    ),
    (
        'App: Finder, window title: "Desktop"\n'
        "It's 8:00 AM on Wednesday, "
        "user has been working for 2 minutes"
    ),
    (
        "App: Discord\n"
        "It's 4:15 PM on Friday, user has been working for 7 hours\n"
        "CPU 9%, RAM 55%"
    ),
    # Hardware stress
    (
        "App: Terminal\n"
        "CPU 98% \u2014 working hard, "
        "RAM 91% \u2014 nearly full, things might start dying"
    ),
    (
        "App: VS Code\n"
        "CPU 75% \u2014 working hard, RAM 85% \u2014 getting crowded\n"
        "Battery 12% \u2014 low, not plugged in"
    ),
    # Time-only blocks
    "It's 9:00 AM on Monday, user has been working for 5 minutes",
    "It's 6:30 PM on Friday, user has been working for 8 hours",
    "It's 2:00 AM on Thursday, user has been working for 4 hours",
    # Idle-only
    "User returned after a 3-minute break",
    "User returned after 20 minutes away",
    "User returned after being away for 2.0 hours",
    # Minimal
    "App: Calculator",
    'App: Zoom, window title: "Meeting"',
    "App: FaceTime",
    "App: Preview",
]

# Conversational user messages for multi-turn training data.
_USER_MESSAGES: list[str] = [
    "hello",
    "hey",
    "what are you doing",
    "say something",
    "what do you think",
    "tell me a joke",
    "are you bored",
    "how's it going",
    "what time is it",
    "any thoughts?",
    "you're quiet",
    "talk to me",
    "what's your mood",
    "roast me",
    "I'm tired",
    "this code is broken",
    "I'm going to bed",
    "what should I do",
    "help",
    "you're funny",
]

# Freeform prompts (no screen context — the model just speaks in character).
_FREEFORM_PROMPTS: list[str] = [
    "Share a random thought.",
    "Say something in character.",
    "What's on your mind?",
    "Muse about something.",
    "Say something unprompted.",
    "Give your take on life.",
    "Complain about something.",
    "Say something dramatic.",
    "What would you say right now?",
    "Express yourself.",
]


@dataclass
class DatasetConfig:
    """Controls how voice lines are converted to training data."""

    min_line_length: int = 15
    max_line_length: int = 200
    train_ratio: float = 0.9
    seed: int = 42
    # Fraction of training samples that are conversation-style (user message)
    conversation_fraction: float = 0.15
    # Fraction that are freeform (no screen context)
    freeform_fraction: float = 0.10


def build_system_prompt(profile: VoiceProfile) -> str:
    """Build a system prompt from the voice profile's persona."""
    base = (
        f"You are {profile.character}, a character who lives in a terminal "
        f"and comments on what the user is doing."
    )
    if profile.persona:
        base += f" {profile.persona}"
    base += (
        "\n\nRules:\n"
        "1. 1-2 sentences. Keep it short.\n"
        "2. Stay in character at all times.\n"
        "3. If nothing interesting is happening, say [SILENT]."
    )
    return base


def _filter_lines(lines: list[str], config: DatasetConfig) -> list[str]:
    """Filter voice lines by length constraints."""
    return [
        line for line in lines
        if config.min_line_length <= len(line) <= config.max_line_length
    ]


def voice_to_conversations(
    profile: VoiceProfile,
    config: DatasetConfig | None = None,
) -> list[dict[str, list[dict[str, str]]]]:
    """Convert voice profile lines into ShareGPT-format conversations.

    Each conversation has system/human/gpt turns. Three types:
    - Observation: screen context → character comment (majority)
    - Conversation: user says something → character responds
    - Freeform: no context, character speaks unprompted
    """
    cfg = config or DatasetConfig()
    rng = random.Random(cfg.seed)

    lines = _filter_lines(profile.lines, cfg)
    if not lines:
        return []

    system_prompt = build_system_prompt(profile)
    conversations: list[dict[str, list[dict[str, str]]]] = []

    n_total = len(lines)
    n_convo = int(n_total * cfg.conversation_fraction)
    n_freeform = int(n_total * cfg.freeform_fraction)
    n_observation = n_total - n_convo - n_freeform

    # Shuffle lines so we get variety in each bucket
    shuffled = lines.copy()
    rng.shuffle(shuffled)

    idx = 0

    # Observation-style: screen context → character comment
    for i in range(n_observation):
        context = rng.choice(_CONTEXT_POOL)
        human_msg = f"What you see right now:\n{context}\n\nYour comment:"
        conversations.append({
            "conversations": [
                {"from": "system", "value": system_prompt},
                {"from": "human", "value": human_msg},
                {"from": "gpt", "value": shuffled[idx]},
            ]
        })
        idx += 1

    # Conversation-style: user message → character response
    for i in range(n_convo):
        context = rng.choice(_CONTEXT_POOL)
        user_msg = rng.choice(_USER_MESSAGES)
        human_msg = (
            f"What you see right now:\n{context}\n\n"
            f'User says: "{user_msg}"\n\nYour response:'
        )
        conversations.append({
            "conversations": [
                {"from": "system", "value": system_prompt},
                {"from": "human", "value": human_msg},
                {"from": "gpt", "value": shuffled[idx]},
            ]
        })
        idx += 1

    # Freeform: no screen context, just speak in character
    for i in range(n_freeform):
        human_msg = rng.choice(_FREEFORM_PROMPTS)
        conversations.append({
            "conversations": [
                {"from": "system", "value": system_prompt},
                {"from": "human", "value": human_msg},
                {"from": "gpt", "value": shuffled[idx]},
            ]
        })
        idx += 1

    # Shuffle final order so types are intermixed
    rng.shuffle(conversations)
    return conversations


def prepare_dataset(
    profile_or_path: VoiceProfile | Path,
    output_dir: Path,
    config: DatasetConfig | None = None,
) -> tuple[Path, Path]:
    """Full pipeline: voice profile → train.jsonl + val.jsonl.

    Returns (train_path, val_path).
    """
    cfg = config or DatasetConfig()

    if isinstance(profile_or_path, Path):
        # Assume the filename stem is the slug
        voices_dir = profile_or_path.parent
        slug = profile_or_path.stem
        profile = load_profile(slug, voices_dir)
    else:
        profile = profile_or_path

    conversations = voice_to_conversations(profile, cfg)
    if not conversations:
        msg = (
            f"No valid lines for {profile.character} "
            f"(need lines between {cfg.min_line_length}-{cfg.max_line_length} chars)"
        )
        raise ValueError(msg)

    # Already shuffled by voice_to_conversations — just split
    split_idx = int(len(conversations) * cfg.train_ratio)
    train_data = conversations[:split_idx]
    val_data = conversations[split_idx:]

    # Write JSONL
    output_dir.mkdir(parents=True, exist_ok=True)
    train_path = output_dir / "train.jsonl"
    val_path = output_dir / "val.jsonl"

    for path, data in [(train_path, train_data), (val_path, val_data)]:
        with path.open("w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return train_path, val_path
