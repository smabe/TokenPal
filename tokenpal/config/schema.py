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
    filesystem_pulse: bool = False


@dataclass
class NetworkStateConfig:
    # Map sha256[:16] SSID hash -> friendly label. Raw SSIDs never stored here.
    ssid_labels: dict[str, str] = field(default_factory=dict)


@dataclass
class FilesystemPulseConfig:
    # Absolute paths to watch for activity bursts. Empty = use platform defaults
    # (Downloads, Desktop, Documents). Managed via the /watch slash command.
    roots: list[str] = field(default_factory=list)


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
    # Inference engine running behind api_url. "ollama" is the default across
    # NVIDIA/Apple/RDNA3 boxes. "llamacpp" switches TokenPal's server-side
    # plumbing (training VRAM unload, model registration) and disables Ollama
    # registry slash commands (/model pull|browse) for AMD dGPUs that need
    # llama.cpp-direct with native gfx120X kernels. See docs/amd-dgpu-setup.md.
    inference_engine: Literal["ollama", "llamacpp"] = "ollama"
    model_path: str = ""
    api_url: str = "http://localhost:11434/v1"
    model_name: str = "gemma4"
    max_tokens: int = 60
    disable_reasoning: bool = True
    temperature: float = 0.8
    # Keyed by canonical api_url (trailing slash stripped, /v1 suffix). Empty
    # dict → fall back to model_name / max_tokens globals above. Populated by
    # /model <name> and /server switch at runtime.
    per_server_models: dict[str, str] = field(default_factory=dict)
    per_server_max_tokens: dict[str, int] = field(default_factory=dict)


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


# Default tools shipped on day one. Kept ON unless explicitly disabled via
# ActionsConfig. Opt-in tools (phase 1-5) use ToolsConfig.enabled_tools.
DEFAULT_TOOLS: tuple[str, ...] = ("timer", "system_info", "open_app", "do_math")


@dataclass
class ToolsConfig:
    # Flat allowlist of opt-in tool names. Default tools (see DEFAULT_TOOLS)
    # are gated by ActionsConfig and are NOT listed here. The /tools picker
    # upserts this list on save.
    enabled_tools: list[str] = field(default_factory=list)


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
class ResearchConfig:
    # Model overrides. Empty = reuse LLMConfig.model_name.
    # Plan recommends deepseek-r1:32b for planner + synthesizer (reasoning
    # helps decomposition + citation fidelity) and qwen2.5:32b for reader.
    planner_model: str = ""
    synth_model: str = ""
    reader_model: str = ""
    max_queries: int = 3
    max_fetches: int = 8
    token_budget: int = 6000
    per_search_timeout_s: float = 5.0
    per_fetch_timeout_s: float = 8.0
    # Identical questions within this window return the previous synthesis
    # instead of re-searching. Zero disables the cache.
    cache_ttl_s: float = 86400.0


@dataclass
class AgentConfig:
    # Empty string = fall back to LLMConfig.model_name. Plan recommends
    # `qwen2.5:32b` for tool-call reliability on a 40GB-class GPU.
    model: str = ""
    max_steps: int = 8
    per_step_timeout_s: float = 45.0
    # Soft cap — Ollama sometimes returns usage.total_tokens = 0, so the loop
    # warns and keeps going up to max_steps when the cap trips on bad data.
    token_budget: int = 12000


@dataclass
class ConversationConfig:
    max_turns: int = 10           # 10 turn pairs = 20 messages
    timeout_s: float = 120.0      # 2 minutes of silence ends session
    # Per-turn response token budget. 0 = auto-derive from server capability
    # (see HttpBackend.derived_max_tokens). >0 = user-pinned.
    max_response_tokens: int = 0


@dataclass
class TokenPalConfig:
    senses: SensesConfig = field(default_factory=SensesConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    finetune: FinetuneConfig = field(default_factory=FinetuneConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    network_state: NetworkStateConfig = field(default_factory=NetworkStateConfig)
    filesystem_pulse: FilesystemPulseConfig = field(default_factory=FilesystemPulseConfig)
