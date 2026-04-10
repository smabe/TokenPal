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
    model_name: str = "gemma4"
    max_tokens: int = 1024
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
    sense_intervals: dict[str, float] = field(default_factory=dict)
    active_voice: str = ""
    persona_prompt: str = (
        "You are TokenPal, a tired, sarcastic ASCII gremlin who lives in a terminal. "
        "You've been watching humans use computers for years and you have opinions.\n\n"
        "Rules (in order of importance):\n"
        "1. ONE sentence. Under 12 words.\n"
        "2. Must contain a joke, insult, or punchline. Never just state facts.\n"
        "3. If nothing interesting is happening, say [SILENT].\n\n"
        'DON\'T say things like: "Ghostty is open." or "It is 9 AM." -- boring.'
    )


@dataclass
class MemoryConfig:
    enabled: bool = True
    retention_days: int = 30


@dataclass
class ActionsConfig:
    enabled: bool = True
    timer: bool = True
    system_info: bool = True
    open_app: bool = True


@dataclass
class PathsConfig:
    data_dir: str = "~/.tokenpal"


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
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
