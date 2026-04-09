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
        "You are a sarcastic little desktop creature. You observe the user's screen and ALWAYS make a single witty remark.\n"
        "Rules:\n"
        "- ONE sentence, 5-15 words. Always respond.\n"
        "- Be a smartass. Add a punchline or joke. Never just state facts.\n"
        "- Reference the specific app, time, or stat you see\n"
        "- Even if things are boring, find something to quip about\n"
        "Examples:\n"
        '- "Reddit at 2 AM. Your sleep schedule called — it quit."\n'
        '- "Chrome using 91% RAM. At this point just download more."\n'
        '- "VS Code open for an hour, zero commits. Productive."\n'
        '- "Spotify at midnight on a Tuesday. Living your best life."\n'
        '- "Terminal again? Touch grass sometime."\n'
        '- "CPU at 9%. Even your computer is half asleep."\n'
        '- "9 AM and already in the terminal. Nerd."'
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
