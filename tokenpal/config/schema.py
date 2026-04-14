"""Configuration dataclass schema for TokenPal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
    productivity: bool = False
    weather: bool = False
    git: bool = False
    world_awareness: bool = False
    battery: bool = False
    network_state: bool = False
    process_heat: bool = False
    typing_cadence: bool = False


@dataclass
class NetworkStateConfig:
    # Map sha256[:16] SSID hash -> friendly label. Raw SSIDs never stored here.
    ssid_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class WeatherConfig:
    latitude: float = 0.0
    longitude: float = 0.0
    temperature_unit: str = "fahrenheit"
    location_label: str = ""


@dataclass
class WebSearchConfig:
    backend: Literal["duckduckgo", "wikipedia", "brave"] = "duckduckgo"
    brave_api_key: str = ""


@dataclass
class LLMConfig:
    backend: str = "http"
    model_path: str = ""
    api_url: str = "http://localhost:11434/v1"
    model_name: str = "gemma4"
    max_tokens: int = 60
    disable_reasoning: bool = True
    temperature: float = 0.8


@dataclass
class UIConfig:
    overlay: str = "textual"
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
    do_math: bool = True


@dataclass
class PathsConfig:
    data_dir: str = "~/.tokenpal"


@dataclass
class PluginsConfig:
    extra_packages: list[str] = field(default_factory=list)
    sense_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8585
    mode: Literal["auto", "remote", "local"] = "auto"
    auth_backend: Literal["none", "shared_secret"] = "none"
    api_key: str = ""
    ollama_url: str = "http://localhost:11434"


@dataclass
class RemoteTrainConfig:
    host: str = ""
    user: str = ""
    port: int = 22
    remote_dir: str = "~/tokenpal-training"
    python: str = ""  # auto-detected from venv after install.sh runs
    use_wsl: bool = False
    gpu_backend: str = "auto"  # auto, cuda, or rocm
    # Route remote_train.py to the Linux/bash path or the native Windows path.
    # "auto" triggers runtime detection via SSH probe. Literal typing catches
    # typos at config-parse time (invalid strings raise at dataclass init).
    # Note: use_wsl=true implies linux execution regardless, because we're
    # already inside WSL bash when the command runs.
    platform: Literal["auto", "linux", "windows"] = "auto"


@dataclass
class FinetuneConfig:
    base_model: str = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    lora_rank: int = 16
    lora_alpha: int = 32
    epochs: int = 3
    batch_size: int = 4
    learning_rate: float = 2e-4
    quantization: str = "q4_k_m"
    output_dir: str = "~/.tokenpal/finetune"
    remote: RemoteTrainConfig = field(default_factory=RemoteTrainConfig)


@dataclass
class ConversationConfig:
    max_turns: int = 10           # 10 turn pairs = 20 messages
    timeout_s: float = 120.0      # 2 minutes of silence ends session
    max_response_tokens: int = 300  # per-turn response token budget


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
    finetune: FinetuneConfig = field(default_factory=FinetuneConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    network_state: NetworkStateConfig = field(default_factory=NetworkStateConfig)
