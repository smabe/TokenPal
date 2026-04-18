# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates witty commentary using a local LLM.

## Architecture
- Dual inference backend: `[llm] inference_engine = "ollama" | "llamacpp"`. Ollama is default (NVIDIA/Apple/RDNA3). llamacpp gates worker VRAM-unload (taskkill vs API), model registration, and `/model pull|browse` slash commands. See `docs/amd-dgpu-setup.md`
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` / `@register_action` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Config loading: location-independent — finds defaults relative to package, searches `~/.tokenpal/config.toml` → project root → cwd
- Threading: async brain loop in daemon thread, Textual UI on main thread. Communication via `post_message()` (thread-safe)
- User input: Textual `Input` widget, routes via `brain.submit_user_input()` (asyncio.Queue + call_soon_threadsafe)
- Senses produce `SenseReading` with `.summary` (natural language, NOT bracketed tags), `.changed_from` (transition metadata), `.confidence`, per-sense `reading_ttl_s`
- Data directory: configurable via `[paths] data_dir` in config (default `~/.tokenpal`), holds logs/, memory.db, voices/

## Key Commands
- Platform installers: `bash scripts/install-macos.sh`, `powershell scripts/install-windows.ps1`, `bash scripts/install-linux.sh` — standalone fresh-machine setup with interactive client/server/both prompt and VRAM-based model recommendation
- `python3 setup_tokenpal.py` — lightweight setup for when Python is already installed. `--client` for remote GPU, `--local` for full local
- `tokenpal` — run the buddy (first-run wizard on fresh install)
- `tokenpal --check` — quick verify: inference engine, model, senses, actions
- `tokenpal --validate` — full preflight: Python version, platform deps, git, inference engine, model, config, senses, macOS permissions
- `tokenpal --verbose` — show debug logs in terminal
- `tokenpal --skip-welcome` -- bypass first-run wizard
- `scripts/download-model.ps1` -- interactive GGUF picker for llamacpp path (Windows). Downloads, updates config + bat.
- `pytest` -- run tests (asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)

## Senses
- `app_awareness` — macOS: Quartz window list (NOT NSWorkspace). Browser titles sanitized (stripped unless matching music player patterns)
- `hardware` — psutil, cross-platform, expressive summaries at high utilization
- `time_awareness` — cross-platform, session duration tracking
- `idle` — pynput, cross-platform, three tiers, transition-only readings
- `weather` — Open-Meteo API (free, no key), poll 30min, TTL 1hr, weight 0.0. Opt-in via `/zip` command or first-run wizard. Geocoding + config write in `tokenpal/config/weather.py`
- `music` — macOS: AppleScript for Music.app/Spotify. Checks `System Events` before querying (prevents auto-launch). Track names redacted from logs
- `productivity` — derives from MemoryStore: time-in-app, switches/hour, streaks. MemoryStore injected via `sense_configs`. Filters sensitive app names
- `git` — cross-platform, polls every 15s via async subprocess. Detects new commits, branch switches, dirty state changes. Uses `asyncio.gather` for parallel git calls. High-signal events bypass the commentary gate for immediate reactions. Opt-in via `[senses] git = true`
- `world_awareness` — HN front-page poll (Algolia API, free/keyless). Poll 30min, TTL 2hr. Emits "Top HN: '...' — N points" only on change. Titles filtered via `contains_sensitive_term`. Opt-in via `[senses] world_awareness = true`. Silent degradation on network failure (no error quip)
- `battery` — psutil.sensors_battery, transition-only (plugged/unplugged/low/critical/full). Auto-disables on desktops where no battery is detected. Opt-in via `[senses] battery = true`
- `network_state` — online/offline, SSID change, VPN up/down. SSID never emitted raw: sha256[:16] hash used for change detection, friendly labels via `[network_state] ssid_labels`. VPN detection is a best-effort interface-prefix heuristic (`utun`/`tun`/`wg`). Opt-in via `[senses] network_state = true`
- `process_heat` — names the top CPU hog when CPU is pinned >80% for 20s. Aggregates Electron-family renderers under parent name (Slack Helper → Slack). Sensitive-app names replaced with "something's working hard". Emits on trigger + on clear. Opt-in via `[senses] process_heat = true`
- `typing_cadence` — WPM buckets (`idle`/`slow`/`normal`/`rapid`/`furious`) from a rolling 30s keypress window with 2-poll hysteresis to prevent flapping. Emits on bucket transitions, at the 10-minute mark of a sustained `rapid`/`furious` run, and on post-burst silence. Subscribes to `tokenpal/senses/_keyboard_bus.py` — a shared singleton pynput listener that also feeds `idle`. The sense itself never imports pynput and never sees key values; test suite enforces this with a source-grep assertion. Opt-in via `[senses] typing_cadence = true`
- `filesystem_pulse` — watchdog-based activity-burst detection for user-configured root directories. Emits "Activity in <leaf-dir>" when ≥5 file events land in a root within 30s, with a 60s cooldown per root to prevent spam. Default roots when `[filesystem_pulse] roots` is empty: `~/Downloads`, `~/Desktop`, `~/Documents` (platform-aware via `Path.home()`). Excludes high-noise dirs (`node_modules`, `.venv`, `.git`, `__pycache__`, `build`, `dist`, `target`, `.next`, `.tox`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `Pods`, `DerivedData`). Privacy: summaries include only leaf directory names — never full paths, never filenames. Opt-in via `[senses] filesystem_pulse = true`

## Actions / Tools Registry
- `@register_action` is the tool-registry decorator. Each `AbstractAction` subclass declares `action_name`, `description`, `parameters` (JSON Schema), `safe: bool`, `requires_confirm: bool`
- Flags `safe` and `requires_confirm` gate future autonomous LLM tool-calling (safe actions with requires_confirm=False can eventually fire without user prompting)
- Built-ins: `timer`, `system_info`, `open_app`, `do_math`. `do_math` proves the registry end-to-end via the `/math` slash command -- uses an ast walker restricted to `BinOp`/`UnaryOp`/numeric `Constant`, never `eval()`
- `ActionResult.display_url`: when set, the orchestrator surfaces the URL as a clickable link in the chat log via `@click` action (Textual handles the click, opens in browser via `webbrowser.open`). `search_web` sets this to `source_url`
- Tool-use debug logging: `--verbose` shows tool round number, action name, arguments (`fmt_args`), and truncated results. Guarded by `isEnabledFor(DEBUG)` to avoid `json.dumps` overhead in production

## Slash Commands (additions)
- `/math <expr>` — evaluate an arithmetic expression (+, -, *, /, //, %, **). Expression length capped, exponent capped. Bypasses the LLM entirely
- `/senses [list|enable <name>|disable <name>]` — inspect + toggle sense flags in config.toml. Writes via `tokenpal/config/senses_writer.py`. Senses are resolved once at startup, so the command always reminds the user to restart
- `/wifi label <friendly name>` — read the current SSID, hash it (sha256[:16]), and upsert `[network_state] ssid_labels` in config.toml. Restart required to apply. Raw SSID never persisted
- `/watch [list|add <path>|remove <path>]` — manage `filesystem_pulse` root directories. `list` shows effective roots (configured or defaults). `add`/`remove` upsert absolute paths in `[filesystem_pulse] roots`. Restart required to apply

## Brain
- `PersonalityEngine`: tiered few-shot examples (anchor lines for recency priming), mood system (6 moods, custom per voice), running gags, guardrails (sensitive apps, late-night tone, cross-franchise filter)
- Three prompt paths: `build_prompt()` (observations), `build_freeform_prompt()` (unprompted thoughts), `build_conversation_prompt()` (user input, single-turn fallback)
- Multi-turn conversation: `ConversationSession` in orchestrator tracks history buffer, `build_conversation_system_message()` + `build_context_injection()` compose the messages array. Config in `[conversation]` section. Observations/freeform suppressed during active session. Session auto-expires after `timeout_s` (default 120s). History capped at `max_turns` pairs (default 10, limited by gemma4's 4-8k context — bump for larger models)
- `ContextWindowBuilder`: per-sense weighted interestingness, acknowledge pattern, composite observations (`_detect_composites()`), public API: `active_readings()`, `prev_summary()`, `ttl_for()`
- Topic roulette: `_pick_topic()` in orchestrator, no 3+ consecutive same-topic, focus hints prepended to context
- Change detection: `changed_from` field on `SenseReading`, app_awareness populates "switched from X"
- Pacing: dynamic cooldown (30-90s based on activity), max 8 comments/5min, forced 2-min silence after 3 consecutive, timing jitter. High-signal sense events (git) bypass the gate entirely
- Freeform thoughts: 15% default, 30% for rich voices (50+ example lines), 45s min gap
- Easter eggs bypass LLM (3:33 AM, Friday 5 PM, Zoom, Calculator)
- Cross-session callbacks: `MemoryStore.get_pattern_callbacks()` detects behavioral patterns (day-of-week skew, first-app-per-session, streaks, startup rituals) from SQLite history. Daily aggregation runs on startup. Callbacks injected into observation prompts as factual one-liners the LLM riffs on. Sensitive apps excluded, results cached per session
- Idle tool rolls: third emission path for quiet stretches. `IdleToolRoller` fires only when the comment gate chose silence, so can't inflate comment rate. Contextual rules invoke a flavor tool (word of the day, moon phase, trivia, etc) and feed the output back into an in-character riff. Running bits let a result ride along subsequent prompts for hours. Full architecture — rule catalog, running-bit model, chain rules, warm cache, telemetry, config, adding a new rule — in `docs/idle-tool-rolls.md`. Read before editing `tokenpal/brain/idle_tools.py`, `tokenpal/brain/idle_rules.py`, or the idle-roll wiring in `orchestrator.py`.
- App enrichment: `tokenpal/brain/app_enricher.py` blocks the first observation tick for a new app (~3s cap) on a `search_web("<app> software")` call and splices the one-sentence result into the snapshot as `App: Cronometer (nutrition tracker)`. Cache in `memory.db`, 30d TTL, 24h retry-backoff on failure. Sensitive apps + a non-app-names set (Finder, WindowServer, etc.) are never enriched. Subsequent ticks for a seen app are instant cache hits
- See `plans/shipped/commentary-finetune.md` for full commentary system design

## UI
- Default overlay: Textual (`tokenpal/ui/textual_overlay.py`). Console and tkinter overlays as fallbacks
- Layout: horizontal split — buddy panel (left) with header/speech/buddy/input/status, scrollable chat log (right). Buddy/input/status are wrapped in `#buddy-footer` (height: auto) so the bubble can never push them off-screen; speech + spacer share `#speech-region` (1fr) which absorbs leftover space
- Chat log shows all buddy comments (observations + conversation) and user messages, with timestamps and voice/user labels separated by `──────` dividers. Uses `Static(markup=True)` with Rich markup; all text escaped via `_esc_markup`, lines accumulated in `_chat_log_lines` (capped at 500). URLs from `/ask` and `search_web` tool render as clickable links via `[@click=app.open_chat_link("idx")]` Textual actions (not OSC 8 -- works in any terminal). Auto-hides when terminal width drops below `buddy.max_frame_width() + _BUDDY_PANEL_PADDING + _CHAT_LOG_MIN_SPACE` (F2 always overrides). `#buddy-panel.min_width` is set dynamically to `max_frame_width() + 4` so frames never wrap, and `#buddy { text-wrap: nowrap; overflow-x: hidden }` crops rather than wraps if the panel is forced narrower
- Status bar: `mood | server | model | voice | app | weather | music | spoke Xs ago` — mood is color-coded
- Keyboard shortcuts: F1=/help, F2=toggle chat log, Ctrl+L=/clear, Ctrl+C=quit
- Speech bubbles: typing animation via `set_interval(0.03)`. SpeechBubbleWidget is a `VerticalScroll` wrapping an inner Static so long bubbles get a scrollbar. Three render tiers picked by `_choose_bubble_variant` and re-picked on resize: bordered (requires `region_w >= _MIN_BORDERED_REGION_WIDTH = 36` and fits in region_h, max_width clamped accounting for `_SPEECH_SCROLL_PADDING = 4`), borderless (plain wrapped text, no border/tail, full region width), or hide. Suppressed bubbles park in `_pending_bubble` and promote via `show_immediate` (no replay-from-zero typing) when the terminal grows. Incoming bubbles arriving mid-animation queue (cap `_MAX_BUBBLE_QUEUE = 3`) so observations don't cut off conversation replies. Chat log always logs every bubble on arrival, so suppression never silently drops content. Voice ASCII art markup is healed at load time in `ascii_renderer._fix_markup` — Rich-only color names (silver/gray/etc) get remapped to hex so Textual's stricter `Style.from_rich_style` doesn't crash with `MissingStyle`
- Thread safety: brain→UI via typed `Message` subclasses + `post_message()`, never `call_from_thread`
- Voice-specific ASCII art: LLM-generated Rich-markup frames (idle, idle_alt, talking) with 4s blink animation

## Slash Commands
- `/help`, `/clear`, `/mood`, `/status`, `/chatlog`
- `/idle_tools [list|on|off|enable <rule>|disable <rule>|roll <rule>]` — inspect + toggle the idle-tool roller rules. See `docs/idle-tool-rolls.md`. Restart required for toggle changes.
- `/gh [log|prs|issues]` — GitHub integration. Runs git/gh CLI in a daemon thread, logs raw output to chat log, then feeds it to the brain so the buddy comments in character
- `/model [name|list|pull|browse]` — model management
- `/voice [train|switch|list|off|info|finetune|finetune-setup|regenerate|ascii|import]` — voice management. `regenerate` refreshes all LLM-backed assets (~60s); `ascii` refreshes only the three ASCII-art frames (idle/idle_alt/talking) so you can iterate on art without re-baking persona/moods/etc.
- `/server [status|switch]` — server connection
- `/zip <zipcode>` — set weather location (geocodes via Open-Meteo, writes to config.toml)
- `/ask <question>` -- web search via DuckDuckGo Instant Answer + Wikipedia REST fallback (free, keyless; Brave API stub). Gated by the `web_fetches` consent category (`~/.tokenpal/.consent.json`); run /consent to grant. Results filtered through `contains_sensitive_term`, wrapped in `<search_result>` delimiters, fed to brain via `submit_user_input()` -- conversation-session follow-up auto-opens. Source URL displayed as clickable link in chat log. Also available as the `search_web` LLM tool (same clickable-link behavior). Search backend abstraction in `tokenpal/senses/web_search/client.py` (BackendName Literal, `_clear_conversation()` zeros history refs on session timeout)
- `/research <question>` — plan → search → fetch → synth → validate pipeline with JSON-structured synth output, substring-grounded claim validation, two-stage fetch (newspaper4k primary, aiohttp fallback). Full architecture in `docs/research-architecture.md` — read before editing any of `tokenpal/brain/research.py`, `tokenpal/actions/research/fetch_url.py`, or the conversation system prompt in `tokenpal/brain/personality.py`.

## LLM Notes
- Default model: `Qwen3-14B-Q4_K_M` on llamacpp path (12-16 GB VRAM tier), `gemma4` on Ollama path. Both support tool calling.
- `disable_reasoning: true` sends `reasoning_effort: "none"`. Without this, gemma4 burns ~900 tokens thinking. Qwen3 uses `<think>` tags which llama-server handles via `--reasoning off` flag (routes all tokens to content, not reasoning_content).
- Response filter: strips asterisks, emojis, leaked tags, prefixes, orphan punctuation. Cross-franchise name filter suppresses responses mentioning characters from wrong show
- Tool calling: OpenAI-compat `/v1/chat/completions` with `tools` parameter. Works on both Ollama and llama-server. `tool_choice` not supported on Ollama.
- Prompt caching: `HttpBackend._apply_cache_hints` auto-sets `cache_prompt=true` on the llamacpp path so llama-server reuses its host-memory KV cache across brain-loop calls with overlapping prefixes. Ollama ignores the flag (has its own `keep_alive` model).
- `max_tokens` auto-derived on connect: `min(context_length // 4, 1024)` via `/api/show` probe (Ollama) or `/props` probe (llamacpp). User pins in `[llm.per_server_max_tokens]` override. Conversation replies that hit the cap auto-continue up to `_MAX_CONTINUATIONS=2` times (re-call with partial text as assistant turn, concat), then trim to last sentence + `...`. Observation path is single-shot. Research synth logs a `warning: synth hit max_tokens` line when `finish_reason=="length"` so truncated-JSON fallbacks are traceable.
- Throughput-aware max_tokens scaling: call sites declare a completion-latency budget (`target_latency_s`) and the backend computes max_tokens from measured decode-TPS and TTFT once a few samples accumulate. Estimators are EWMAs (α=0.2) keyed by (api_url, model) and persisted to `memory.db` so a known rig skips the 3-call bootstrap on restart. Resolution order: explicit `max_tokens` → user pin → (target − ttft) × decode_tps → static default. See `plans/shipped/gpu-scaling.md` before editing `_resolve_max_tokens`, `_record_sample`, or the orchestrator call-site wiring.
- Model auto-adopt: `HttpBackend._try_connect()` probes `/v1/models` and adopts the server's first model when the client has no per-server override. Disabled on the local Ollama fallback path to avoid grabbing random trained models.

## Voice Training
- `/voice train`, `/voice regenerate` — structured persona cards with catchphrase priming and cross-franchise guardrails
- ASCII art generation: LLM returns a small JSON classification (skeleton name + 5-color hex palette + eye/mouth glyphs), which is rendered against one of 8 hand-drawn skeleton templates in `tokenpal/ui/ascii_skeletons.py`. Franchise context from `profile.source` is passed to the classifier so it can pick canonical colors. Three frames (idle, idle_alt with blink eye, talking with open mouth) are all rendered from the same skeleton via slot substitution and stored in voice profile JSON as `ascii_idle`, `ascii_idle_alt`, `ascii_talking`. Read `docs/voice-training.md` and `ascii_skeletons.py` before editing either the classifier prompt or the templates
- See `docs/voice-training.md` for persona format, anchor lines, banned names, and architecture

## Fine-Tuning
- Remote LoRA fine-tuning via SSH. Recommended: `google/gemma-2-2b-it` on RTX 4070 (~15 min Windows, ~7 min Linux)
- Two platform paths: native Windows (PowerShell) and Linux/WSL (tmux). ROCm works for RDNA 3; RDNA 4 blocked until ROCm 7.3+
- Pipeline: build wheel -> push bundle -> install -> push base model -> prep data -> train -> merge -> pull -> register (Ollama path: `ollama create`; llamacpp path: GGUF conversion deferred to M4)
- See `docs/remote-training-guide.md` for setup, config, troubleshooting, and developer gotchas

## Server
- `tokenpal/server/` package: FastAPI inference proxy + training orchestration
- Byte-forwarding `/v1/*` proxy to local inference engine (Ollama or llama-server, both bind 11434). Streaming-ready.
- `create_app()` accepts `inference_url` + `inference_engine` params. `ollama_url` kept as deprecated alias for one release.
- Auto-fallback: server unreachable -> tries `localhost:11434/v1` (does not auto-adopt models from fallback)
- Launch scripts: `start-server.bat` (Ollama path) or `start-llamaserver.bat` (llamacpp path, includes `-ngl 99 -c 8192 -np 1 --no-mmap --jinja --reasoning off`, auto-kills llama-server on exit)
- `scripts/download-model.ps1` -- interactive GGUF picker for the llamacpp path. Downloads from HF, updates config.toml + start-llamaserver.bat.
- See `docs/server-setup.md` for Ollama setup, `docs/amd-dgpu-setup.md` for llamacpp setup

## Privacy
- No clipboard monitoring (explicitly rejected)
- Sensitive app exclusion: banking, passwords, health, messaging — goes silent
- Browser window titles sanitized (stripped unless matching music player patterns)
- Session memory stores only app names and timestamps, never content
- During active conversations, user messages are held in memory (not saved to disk) until the session times out (~2 min of silence). Conversation buffer is cleared on sensitive app detection. User input truncated to 30 chars in log output
- Log files and memory.db at 0o600 (owner-only)
- Music track names redacted from DEBUG logs
- Network senses/commands — all opt-in, all keyless by default: `weather` (Open-Meteo), `world_awareness` (HN Algolia), `/ask` (DuckDuckGo + Wikipedia; Brave via `TOKENPAL_BRAVE_KEY` env var stubbed). All untrusted external text wrapped in delimiters + filtered via `contains_sensitive_term` before any prompt composition. `/ask` shows an explicit first-use consent warning; queries never persisted to disk
- Lat/lon rounded to 1 decimal. No ip-api.com. No clipboard monitoring. No mic/audio sensing

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent`
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace`)
- Textual overlay is default (cross-platform including Windows). Console and tkinter as fallbacks
- `config.toml` is gitignored -- each machine has its own
- Windows + RDNA 4 (gfx1201): Ollama's Vulkan backend produces wrong numerics on dense models; ROCm backend can't enumerate the card. Use `[llm] inference_engine = "llamacpp"` with lemonade-sdk's llama.cpp-rocm build. See `docs/amd-dgpu-setup.md`

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides in `docs/dev-setup-*.md`
- Plan files in `plans/` — active plans track current work, shipped plans in `plans/shipped/`
- Open issues: `gh issue list`
