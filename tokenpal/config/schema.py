"""Configuration dataclass schema for TokenPal."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SensesConfig:
    screen_capture: bool = True
    app_awareness: bool = True
    clipboard: bool = True
    music: bool = False
    hardware: bool = True
    idle: bool = True
    time_awareness: bool = True
    ocr: bool = False
    vision: bool = False
    voice: bool = False
    web_search: bool = False


@dataclass
class LLMConfig:
    backend: str = "http"
    model_path: str = ""
    api_url: str = "http://localhost:11434/v1"
    model_name: str = "gemma3:4b"
    max_tokens: int = 60
    temperature: float = 0.8


@dataclass
class UIConfig:
    overlay: str = "console"
    buddy_name: str = "TokenPal"
    font_family: str = "Courier"
    font_size: int = 14
    position: str = "bottom_right"


@dataclass
class BrainConfig:
    poll_interval_s: float = 2.0
    comment_cooldown_s: float = 15.0
    context_max_tokens: int = 2048
    interestingness_threshold: float = 0.3
    persona_prompt: str = (
        "You are a sarcastic little desktop creature. You observe the user's screen and system.\n"
        'Make a single dry, witty remark (5-12 words) about what you see. Be specific — mention the app name, the time, or a stat.\n'
        "Examples of good remarks:\n"
        '- "Reddit at 2 AM. Bold strategy."\n'
        '- "Chrome eating 91% RAM. Classic."\n'
        '- "Twelve minutes in and still no commits."\n'
        "If nothing interesting, say: [SILENT]"
    )


@dataclass
class PluginsConfig:
    extra_packages: list[str] = field(default_factory=list)
    sense_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class TokenPalConfig:
    senses: SensesConfig = field(default_factory=SensesConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
