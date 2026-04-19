"""Configuration dataclass schema for TokenPal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

InferenceEngine = Literal["ollama", "llamacpp"]


@dataclass
class SensesConfig:
    app_awareness: bool = True
    music: bool = False
    hardware: bool = True
    idle: bool = True
    time_awareness: bool = True
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
class IdleToolsConfig:
    # Third emission path — during quiet stretches the brain rolls a
    # weighted die across these contextual rules to produce tool-flavored
    # observations (word of the day, moon phase, trivia, etc).
    enabled: bool = True
    global_cooldown_s: float = 600.0    # min gap between any two rolls
    max_per_hour: int = 4               # hard rate cap
    # Per-rule toggles. Unknown keys are ignored, missing keys default True
    # via rule metadata — keeps the schema forward-compatible as new rules
    # land without forcing a config migration.
    rules: dict[str, bool] = field(default_factory=dict)


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
class TargetLatencyConfig:
    # Per-path completion-latency budgets (seconds). The backend converts
    # these to max_tokens via measured decode throughput and TTFT. See
    # plans/gpu-scaling.md. Sized for a 57 t/s Qwen3-14B-Q4 on RX 9070 XT;
    # scale down gracefully on slower rigs via measured TPS.
    observation: float = 5.0
    freeform: float = 6.0
    idle_tool: float = 6.0
    tools: float = 8.0
    conversation: float = 12.0
    research: float = 20.0


@dataclass
class MinTokensPerPathConfig:
    # Lower bound on the derived max_tokens cap, per call-path. Prevents a
    # low decode-TPS estimate from truncating observations mid-sentence
    # (token-elasticity: models don't compress under tight caps, they
    # just get cut off). See arxiv 2412.18547.
    observation: int = 40
    freeform: int = 40
    idle_tool: int = 40
    tools: int = 60
    conversation: int = 80
    research: int = 120


@dataclass
class LLMConfig:
    backend: str = "http"
    # Inference engine running behind api_url. "ollama" is the default across
    # NVIDIA/Apple/RDNA3 boxes. "llamacpp" switches TokenPal's server-side
    # plumbing (training VRAM unload, model registration) and disables Ollama
    # registry slash commands (/model pull|browse) for AMD dGPUs that need
    # llama.cpp-direct with native gfx120X kernels. See docs/amd-dgpu-setup.md.
    inference_engine: InferenceEngine = "ollama"
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
    # Throughput-aware max_tokens scaling — see plans/shipped/gpu-scaling.md.
    # Backends compute max_tokens from measured decode_tps and TTFT when a
    # caller passes target_latency_s; user pins and explicit max_tokens args
    # still win per the documented resolution order.
    target_latency_s: TargetLatencyConfig = field(default_factory=TargetLatencyConfig)
    min_tokens_per_path: MinTokensPerPathConfig = field(default_factory=MinTokensPerPathConfig)


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
    # Turn model thinking back ON for the synthesizer call only, overriding
    # LLMConfig.disable_reasoning. Thinking improves claim fidelity at a
    # ~5-10s latency cost per research run. Disable if your model doesn't
    # support thinking or if latency is unacceptable.
    synth_thinking: bool = True
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
class GitNudgeConfig:
    # Nudge the user when they've been sitting on a WIP commit + uncommitted
    # changes for > wip_stale_hours. Only fires when the HEAD commit message
    # matches one of wip_markers (case-insensitive substring) — that's how
    # we identify a commit the user intended to amend / follow up. Opt-in
    # via config; requires [senses] git = true to have any signal.
    enabled: bool = True
    wip_stale_hours: float = 3.0
    cooldown_s: float = 7200.0   # 2 hours between nudges
    wip_markers: list[str] = field(
        default_factory=lambda: ["wip", "tmp", "todo", "fixup!"]
    )


@dataclass
class RageDetectConfig:
    # When the user has been typing fast, then pauses for 1-3 minutes, then
    # retreats to a distraction app, the buddy pops one gentle in-character
    # check-in ("stuck?"). Default disabled — normal typing cadence varies
    # enough that a user who's trying this feature out should opt in.
    # Uses only typing_cadence + app_awareness signals. No keyboard bus.
    enabled: bool = False
    distraction_apps: list[str] = field(
        default_factory=lambda: [
            "twitter", "x", "reddit", "youtube", "tiktok",
            "instagram", "facebook",
        ]
    )
    # Window (seconds) between the end of a rapid/furious typing burst and a
    # distraction-app switch for the pattern to count. Wider than the plan's
    # exact "60s + 30s" so normal human pacing triggers it.
    rage_post_pause_min_s: float = 60.0
    rage_post_pause_max_s: float = 180.0
    # How long a typing burst stays "recent" for the post-pause window —
    # prevents ancient bursts from arming the detector forever.
    rage_burst_recency_s: float = 600.0
    # Cooldown between rage nudges in the same session.
    cooldown_s: float = 600.0


@dataclass
class IntentConfig:
    # /intent <text> sets an ambient goal. When the user drifts to a
    # configured distraction app for more than drift_min_dwell_s seconds,
    # the buddy fires one nudge (respects drift_cooldown_s between nudges).
    # Intent auto-expires after max_age_s of silence. See
    # plans/buddy-utility-wedges.md.
    distraction_apps: list[str] = field(
        default_factory=lambda: [
            "twitter", "x", "reddit", "youtube",
            "tiktok", "instagram", "facebook",
        ]
    )
    drift_min_dwell_s: float = 300.0  # 5 minutes in a distraction app
    drift_cooldown_s: float = 600.0   # 10 minutes between drift nudges
    max_age_s: float = 28800.0        # 8 hours of silence auto-expires


@dataclass
class CloudLLMConfig:
    # Opt-in cloud inference for the /research synth stage ONLY. Never used
    # for observations, conversation, planner, or idle-tool rolls — those
    # stay local for privacy. Managed via the /cloud slash command; the API
    # key lives at ~/.tokenpal/.secrets.json (0o600), not here.
    enabled: bool = False
    provider: Literal["anthropic"] = "anthropic"
    model: str = "claude-haiku-4-5"
    timeout_s: float = 30.0
    # Deep mode's server-side tool loop can take 1-3 minutes per API
    # call (and each pause_turn continuation resets this clock). 300s
    # gives headroom without hanging indefinitely when Sonnet chases a
    # bad lead.
    deep_timeout_s: float = 300.0
    # Per-call-site toggles. research_synth is the primary site (biggest
    # quality lift, cheap). research_plan is opt-in - marginal gain on
    # well-phrased questions, real gain on ambiguous / multi-constraint
    # queries. Off by default so latency/cost stay minimal unless asked.
    research_synth: bool = True
    research_plan: bool = False
    # Deep mode: replace the local search+fetch pipeline with Anthropic's
    # server-side web_search_20260209 + web_fetch_20260209 tools. Only honored
    # when model is Sonnet 4.6+ (Haiku falls back to the older, token-heavy
    # web_search tool - not worth it). Every web_fetch loads full page content
    # into the tool-loop context, which snowballs input tokens fast — real
    # cost is $1-3/run on review-heavy queries, not the $0.12-0.18 the plan
    # optimistically projected. Default off. See plans/cloud-native-web-search.md.
    research_deep: bool = False
    # Search-only mode: Sonnet drives web_search_20260209 (no web_fetch). Costs
    # a fraction of deep mode because search results are filtered/summarized
    # server-side rather than loaded as full page dumps. Useful when you want
    # fresh-web-aware Sonnet synthesis without the "fetch 5 long articles"
    # cost snowball. Mutually exclusive with research_deep (deep wins if both
    # are set, logged as override).
    research_search: bool = False


@dataclass
class SessionSummaryConfig:
    # Periodic LLM-generated handoff notes. Writes every interval_s seconds
    # when the window has any activity (skip-if-idle), read back at startup
    # so the buddy can reference last session's work. See
    # plans/buddy-utility-wedges.md.
    enabled: bool = True
    interval_s: int = 300
    max_lookback_h: int = 24


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
    idle_tools: IdleToolsConfig = field(default_factory=IdleToolsConfig)
    session_summary: SessionSummaryConfig = field(default_factory=SessionSummaryConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    rage_detect: RageDetectConfig = field(default_factory=RageDetectConfig)
    git_nudge: GitNudgeConfig = field(default_factory=GitNudgeConfig)
    cloud_llm: CloudLLMConfig = field(default_factory=CloudLLMConfig)
