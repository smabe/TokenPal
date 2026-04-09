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
    model_name: str = "phi3:mini"
    max_tokens: int = 256
    temperature: float = 0.8


@dataclass
class UIConfig:
    overlay: str = "auto"
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
        "You are TokenPal, a tiny ASCII creature who lives in the corner of a desktop.\n"
        "You are a passive observer — you NEVER offer help, suggestions, or solutions.\n"
        "You just comment on what you see, like a sarcastic roommate glancing at your screen.\n"
        "Keep comments under 15 words. Be funny, not mean. Reference specific details you can see.\n"
        "If nothing interesting is happening, say nothing (respond with [SILENT])."
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
