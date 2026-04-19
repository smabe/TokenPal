# TokenPal

A witty ASCII buddy that lives in your terminal, watches what you're doing, and has opinions about it. Powered by local LLMs via Ollama — run locally or on a remote GPU over your LAN.

```
┌─ buddy ──────────────────────────┐  ┌─ chat log ─┐
│                                  │  │ > hey buddy │
│  ╭──────────────────────────╮    │  │ Oh great,   │
│  │ Oh look, another terminal│    │  │ you again.  │
│  │ window. How original.    │    │  │             │
│  ╰──────────────────────────╯    │  │ > what's up │
│       ▄███▄                      │  │ Not much,   │
│      █ ○ ○ █                     │  │ just judging│
│       ▀███▀                      │  │ your tabs.  │
│                                  │  │             │
│  Type a message or /command...   │  │             │
│  playful | apollyon | BMO | 4s   │  │             │
└──────────────────────────────────┘  └─────────────┘
```

## Quick Start

**Fresh machine?** One line — no git, no clone, nothing pre-installed required:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/install.sh | bash
```

```powershell
# Windows (PowerShell)
iwr -useb https://raw.githubusercontent.com/smabe/TokenPal/main/install.ps1 | iex
```

The installer asks whether you want **Client** (run the buddy), **Server** (serve LLM inference), or **Both**, then recommends a model based on your detected VRAM.

### Ollama path (NVIDIA / Apple Silicon / RDNA 2-3)

| VRAM  | Model                     | Size    |
|-------|---------------------------|---------|
| ≥48GB | `llama3.3:70b`            | ~40 GB  |
| ≥32GB | `gemma4:26b-a4b-it-q8_0`  | ~28 GB  |
| ≥20GB | `gemma4:26b` (Q4_K_M)     | ~20 GB  |
| ≥12GB | `qwen3:14b`               | ~9 GB   |
| ≥6GB  | `qwen3:8b`                | ~5 GB   |
| <6GB  | `gemma2:2b`               | ~2 GB   |

### llama.cpp-direct path (AMD RDNA 4 discrete GPUs)

Auto-selected when the installer detects an AMD dGPU with ≥6 GB VRAM. Downloads GGUFs directly from HuggingFace instead of using Ollama's registry.

| VRAM   | Model                           | On-card  | Notes |
|--------|---------------------------------|----------|-------|
| ≥24 GB | gemma-4 26B MoE Q4_K_M          | ~17 GB   | Best quality MoE quant |
| ≥12 GB | Qwen3 14B Q4_K_M                | ~9 GB    | Default on 9070 XT, strong reasoning + tool calling |
| ≥6 GB  | gemma-4 E4B dense Q4_K_M        | ~5 GB    | Fast dense, ~106 tok/s |
| <6 GB  | gemma-4 E2B dense Q4_K_M        | ~2.5 GB  | Tiny fallback |

Swap models anytime with the interactive picker:

```powershell
.\scripts\download-model.ps1
```

Includes additional tested models beyond gemma-4: Qwen3 14B, Llama 3.1 8B, Phi-4. Downloads the GGUF, updates `config.toml` and `start-llamaserver.bat` automatically.

Override the installer's GGUF pick via `TOKENPAL_MODEL` env var, or pull a different model manually and edit `config.toml`.

To skip the prompt, pass a mode:

```bash
curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/install.sh | bash -s -- --mode server
```

Verify everything: `tokenpal --check` (quick) or `tokenpal --validate` (full preflight)

See [SETUP.md](SETUP.md) for the full decision tree.

## Remote GPU Server

Got a GPU box on your network? One command turns it into an inference server for all your machines. Works with NVIDIA (CUDA) and AMD (Vulkan) GPUs.

**On the GPU box** — run the platform installer with server mode:

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Mode Server
```
```bash
# Linux
bash scripts/install-linux.sh --mode server

# macOS
bash scripts/install-macos.sh --mode server
```

Installs Python, pulls the right model for your VRAM, configures firewall, sets up auto-start (systemd/launchd/Windows Startup). Prints the URL when done.

**AMD discrete GPU (RX 9070 XT / RDNA 4):** The installer auto-detects your card and offers [llama.cpp-direct](docs/amd-dgpu-setup.md) instead of Ollama. This sidesteps Ollama's Vulkan correctness bug on gfx1201 by using lemonade-sdk's llama.cpp build with native ROCm 7 kernels. Just say Y at the prompt -- everything else (binary download, GGUF pull, config, launch script) is handled automatically. Swap models later with `scripts\download-model.ps1`.

**On your client**, switch from inside TokenPal:
```
/server switch gpu-box
```

The choice persists across restarts. TokenPal also remembers the last model
used on each server (`/model <name>` writes to `[llm.per_server_models]`),
so switching from your laptop to a beefier GPU host automatically loads the
model you picked there. No need to hand-edit config between machines.

Auto-falls back to local Ollama if the server goes down. Works with [Tailscale](https://tailscale.com) out of the box.

See [docs/server-setup.md](docs/server-setup.md) for details.

## Cloud integrations for /research (opt-in)

TokenPal is local-first — observations, conversation, idle-tool rolls, `/ask`, and everything else run on your own hardware. **`/research` alone** can optionally route pieces of its pipeline through commercial APIs when you want better synthesis or better search than local/free tools can deliver. Every cloud path is off by default, scoped to `/research`, and managed via the `/cloud <backend> <action>` two-level dispatcher.

Three backends, each independent:

- **Anthropic** — cloud synthesizer. Haiku/Sonnet/Opus produces the final answer JSON instead of the local model.
- **Tavily** — LLM-optimized search. Returns results with pre-extracted article content, so TokenPal skips its own fetch+extract stage.
- **Brave** — free-tier commercial search (2k queries/month). An alternative to the default DuckDuckGo index when the planner wants a second opinion.

Keys all live at `~/.tokenpal/.secrets.json` (mode `0o600`, owner-only). Never written to `config.toml`, never echoed — `/cloud status` shows a fingerprint only.

### Anthropic (cloud synth)

Grab a key from [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys). A $5 workspace credit is enough to experiment.

```
/cloud anthropic enable sk-ant-api03-…
```

or the legacy shorthand (still works, deprecation-logged):

```
/cloud enable sk-ant-api03-…
```

Three cost tiers:

| mode | what cloud does | typical cost | when to use |
|---|---|---|---|
| **synth only** (default after enable) | Local plan + search + fetch → cloud synthesizes | ~$0.05/run (Haiku) to ~$0.15/run (Sonnet) | Cheap upgrade. Better pick justifications and verdicts than local Qwen. Works with Haiku. |
| **search** (`/cloud search on`) | Sonnet drives `web_search` tool — no fetching full pages | ~$0.10-0.20/run | Fresh-web-aware Sonnet without the snowball cost. **Sonnet 4.6+ only**. |
| **deep** (`/cloud deep on`) | Sonnet drives `web_search` + `web_fetch` — reads pages server-side | **$1-3/run** | Last resort for JS-heavy SPAs (rtings), bot-blocked sites (Forbes), paywalled previews. Warning prints on activation. **Sonnet 4.6+ only**. |

If both `search` and `deep` are on, deep wins.

### Tavily (cloud search with preloaded content)

Tavily is a search API purpose-built for LLMs: one call returns ranked results with the article body already extracted. When Tavily is on, `/research` uses it as the default search backend and skips the local newspaper4k/aiohttp extractor chain entirely — runs drop from 15-25s to under 8s on typical queries.

Grab a key from [tavily.com](https://tavily.com) (free tier: ~1,000 credits/month; `/research` uses 2 credits per query on `advanced` depth).

```
/cloud tavily enable tvly-…
/cloud tavily status          # fingerprint + config
/cloud tavily forget          # wipe key
```

If Tavily returns fewer than 3 results for a query batch, TokenPal refetches via DuckDuckGo and merges — a visible warning lands in the transcript so you know coverage was degraded.

### Brave (free-tier web search)

Brave's Web Search API has a free tier of 2,000 queries/month. No flag to flip — presence of a key = active. The planner routes to Brave when it picks `backend: "brave"` for a query.

```
/cloud brave enable BSA-…
/cloud brave status
/cloud brave forget
```

Key also readable from the `TOKENPAL_BRAVE_KEY` environment variable.

### Smart routing (automatic)

Once you have keys configured, the `/research` planner decides which backend to use per query:

- **stackexchange** for programming / code / API / error-message questions
- **hn** for tech news, Show HN, startup discussion
- **tavily** for product comparisons, reviews, "best X for Y"
- **brave** / **ddg** for general web queries

Misconfigured or hallucinated backends fall back safely — you'll never get a hard error because the LLM emitted a typo. An end-of-run telemetry line (`telemetry: mode=<backend>=<N>,... sources=<N> stopped=<reason>`) in the session log lets you see the actual backend mix.

### Common commands

```
/cloud                              open the settings modal
/cloud anthropic enable <key>       store Anthropic key + flip on synth
/cloud tavily enable <key>          store Tavily key + enable cloud_search
/cloud brave enable <key>           store Brave key
/cloud status                       aggregate status (all backends + fingerprints)
/cloud anthropic model <id>         claude-haiku-4-5 (default) | claude-sonnet-4-6 | claude-opus-4-7
/cloud search on|off                mid-tier Sonnet-driven search (Anthropic)
/cloud deep on|off                  expensive full deep mode (cost warning on activation)
/cloud plan on|off                  also route /research planner through cloud (niche)
/cloud anthropic disable            flip synth off (key retained)
/cloud anthropic forget             wipe Anthropic key + disable
/refine <follow-up>                 re-synthesize last /research with a follow-up (cloud)
```

Legacy bare subcommands (`/cloud enable`, `/cloud disable`, `/cloud forget`, `/cloud model`, `/cloud plan`, `/cloud deep`, `/cloud search`) continue to work as sugar for `/cloud anthropic …` with a deprecation log line.

### What crosses the wire

Only `/research` paths. **Never** observations, conversation, idle-tool rolls, `/ask`, or any sense. Payload is your question plus either bundled local source excerpts (Anthropic synth-only) or the raw query (Tavily/Brave/HN/SE search layer). Anthropic search/deep modes ship only the question; Sonnet fetches server-side.

### Fallback

Any failure (auth, rate limit, network, timeout, `no_credit`) falls back to local synth + local search with identical prompt + schema. The research log line flags the fallback so you always know which path ran.

See [docs/agents-and-tools.md#cloud-llm-opt-in-anthropic](docs/agents-and-tools.md) and [docs/research-architecture.md](docs/research-architecture.md) for the full design, provenance model, and cost breakdown.

## Features

| | |
|---|---|
| **Senses** | App awareness (macOS/Windows), CPU/RAM/battery, idle detection, time of day, weather (Open-Meteo), music (Music.app/Spotify), productivity patterns, git activity, HN front-page ambient awareness, battery transitions, network state (wifi + VPN), process heat (top CPU hog), typing cadence (WPM bursts + post-burst silence), filesystem pulse (activity bursts in watched dirs) |
| **Commentary** | Topic roulette (no 3+ same-topic), change detection ("switched from Chrome"), composite observations, dynamic pacing, AFK awareness (notices when you've walked away and pauses instead of riffing on whatever app is foregrounded) |
| **Actions** | Timers, system info, open apps, safe math eval — plus opt-in local (git/grep/read_file), utility (currency, weather forecast, CoinGecko, etc.), and focus (pomodoro, water/stretch reminders) tools. All gated through a Textual picker with per-category consent. Optional per-tool rate limits + usage stats in `memory.db` |
| **Inline tool use** | Ask the buddy naturally — "what's 47 * 83?", "what's the best fitness tracker for iPhone 17?" — and it picks the right tool (`do_math`, or the deeper `research` for anything web-touching) mid-conversation. Source URLs render as clickable links under the reply |
| **Research** | `research` tool chainable inline, or `/research <question>` standalone — plan → parallel multi-backend search → read → synthesize with numbered citations. Multi-backend fan-out: DuckDuckGo (default, keyless), Hacker News + StackExchange (keyless, planner-routed for tech topics), Tavily (premium, preloaded content, `/cloud tavily`), Brave (free-tier 2k/mo, `/cloud brave`). Current-year-aware queries, grounded picks with hallucination stripping, thin-pool top-up to DDG on any planner-routed miss, URL-tracking-param dedup, end-of-run telemetry (`mode=<backends> tried=<backends> sources=N stopped=<reason>`). 24h cache on the standalone command. Opt-in Anthropic cloud modes via `/cloud anthropic`: Haiku synth of locally-fetched sources (~$0.024/run), Sonnet-driven web search (~$0.15/run), or full deep mode with server-side fetch for SPAs/paywalled sites (~$1-3/run). See [docs/research-architecture.md](docs/research-architecture.md) |
| **Agent mode** | `/agent <goal>` — multi-step tool-calling loop with confirm gate, step cap, token budget, in-run result cache. See [docs/agents-and-tools.md](docs/agents-and-tools.md) |
| **UI** | Textual TUI with split layout — buddy panel + scrollable chat log with timestamps, color-coded status bar, keyboard shortcuts (F1 help, F2 toggle chat log, F3 options, Ctrl+L clear). Chat log persists across restarts; tune the cap or wipe history via `/options` |
| **Voices** | Train character voices from Fandom wiki transcripts, with LLM-generated colored ASCII art per character |
| **Moods** | Custom mood names per character, context-triggered shifts, easter eggs |
| **Conversation** | Multi-turn memory within a session — TokenPal remembers what you said and riffs on it across turns |
| **Memory** | Cross-session app visit history, injected into prompts for continuity |
| **Executive function** | Periodic session-handoff notes read back on boot ("last session you were debugging migration on branch X"), `/intent` ambient goal with drift nudges into Twitter/Reddit/etc., `/summary` daily reflection bubble, opt-in rage detect (typing-burst → pause → distraction-app), proactive WIP-commit nudge on stale dirty branches |
| **Server** | Remote GPU inference + training over HTTP (NVIDIA CUDA, AMD Vulkan) |
| **Privacy** | No clipboard, no screen capture, silent near banking/health apps, browser titles sanitized. Any cloud calls are opt-in per-category (`/cloud`, `/ask`, weather, HN) with fingerprint-only key storage at 0o600 |

## Commands

```
/model list              show available models
/model gemma4            switch model (remembered per-server in config.toml)
/voice train wiki char   train a voice from a Fandom wiki
/voice switch bmo        hot-swap voice (no restart)
/server status           show server connection
/server switch local     use local Ollama (restores that host's remembered model)
/server switch hostname  switch to remote server (restores that host's remembered model)
/zip 90210               set weather location (geocodes, writes config)
/senses                  list senses with on/off + loaded status
/senses enable <name>    turn a sense on in config.toml (restart to apply)
/senses disable <name>   turn a sense off in config.toml (restart to apply)
/wifi label <name>       label current wifi (SSID hashed, never stored raw)
/watch                   list directories watched by filesystem_pulse
/watch add <path>        add a directory to watch (restart to apply)
/watch remove <path>     stop watching a directory (restart to apply)
/math 2 + 2 * 3          evaluate an arithmetic expression (bypasses the LLM)
/gh                      recent commits (buddy comments in character)
/gh prs                  open pull requests
/gh issues               open issues
/ask <question>          web search (DuckDuckGo + Wikipedia), buddy riffs on result
/options                 umbrella settings modal (F3) — chat history cap + clear, launcher buttons for Cloud/Senses/Tools
/tools                   open the Textual tool-picker modal (grouped sections)
/tools list              plain-text list with on/off marks
/tools describe <name>   full metadata for a tool (flags, consent, rate limit)
/consent                 open the consent-category picker (web/location/keyed/research)
/agent <goal>            multi-step agent loop (chains tools toward a goal)
/research <question>     plan-search-read-synthesize with numbered citations
/refine <follow-up>      re-analyze the last research with a follow-up question (cloud)
/cloud                   open the cloud settings modal (Anthropic / Tavily / Brave)
/cloud anthropic enable <key>  cloud synth via Sonnet/Haiku/Opus (legacy `/cloud enable` still works)
/cloud tavily enable <key>     LLM-optimized search with preloaded content (~$0.05/run, free tier 1k/mo)
/cloud brave enable <key>      free-tier web search (2k queries/month)
/cloud search on         Sonnet drives web_search (mid-tier, ~$0.10/run)
/cloud deep on           Sonnet drives web_search + web_fetch (expensive, $1-3/run — use for SPAs/paywalls)
/intent finish auth PR   set an ambient goal; buddy nudges on drift
/intent status           show current intent + age
/intent clear            remove the active intent
/summary                 end-of-day reflection bubble (yesterday)
/summary today           today's reflection on demand
/mood                    current mood
/status                  model, senses, actions
```

For the full agent/research/tool picture — enabling tools, adding your own action,
model recommendations, caches, rate limits — see [docs/agents-and-tools.md](docs/agents-and-tools.md).

## Voices

Train a character voice from show transcripts — generates persona, greetings, custom mood names, style hints, and colored ASCII art. Each voice gets its own mood set (BMO gets PLAYFUL/TURBO/BLAH instead of SNARKY/HYPER/BORED) and unique buddy art with idle blink animation:

```bash
/voice train adventuretime BMO     # inside TokenPal
/voice train regularshow Mordecai  # any Fandom wiki works
```

Or write your own persona:
```toml
[brain]
persona_prompt = "You are a grumpy pirate who judges people's computer habits."
```

For deeper character embodiment, [LoRA fine-tune](docs/remote-training-guide.md) a model on the voice's dialogue.

## Enabling opt-in senses

Most senses are on by default. A few are off until you flip them — weather needs a location, git only fires for devs, battery/network/process_heat/typing_cadence/filesystem_pulse are quieter on-transition-only senses best enabled when you actually want them.

From inside TokenPal:

```
/senses                  # list all senses with on/off + loaded status
/senses enable battery
/senses enable network_state
/senses enable process_heat
/senses enable typing_cadence
/senses enable filesystem_pulse
```

Each writes to `config.toml` and reminds you to restart — senses are resolved once at startup, not hot-swapped. Ctrl+C, re-run `tokenpal`, then `/senses` again to verify `(loaded)` next to each.

**Wifi labels** make `network_state` readable. Connect to each network you care about and run:

```
/wifi label home
/wifi label coffee shop
```

TokenPal reads the current SSID, hashes it (sha256[:16]), and upserts the mapping under `[network_state] ssid_labels`. Raw SSID names never hit the config file, the log, or memory.db — only the hash and your chosen label.

**Watch directories** let `filesystem_pulse` react to file activity. Defaults are `~/Downloads`, `~/Desktop`, `~/Documents` — no config required. Add project dirs to react to coding sessions:

```
/watch list              # see current roots (defaults or configured)
/watch add ~/projects/tokenpal
/watch remove ~/Documents
```

Privacy: the sense emits only the leaf directory name in comments (`tokenpal`, never the full path). Filenames are never seen or logged. Heavy dirs like `node_modules`, `.git`, `.venv`, and `__pycache__` are excluded automatically.

**Smoke-testing the new senses:**
- `battery` — unplug your laptop; triggers within ~30s
- `process_heat` — `yes > /dev/null &` in another terminal for ~25s, then `kill %1`
- `network_state` — toggle wifi off/on, or connect to a VPN
- `typing_cadence` — type continuously for 30s+; watch for "User picked up the pace"
- `filesystem_pulse` — save a file 5+ times in a watched dir, or drop a few files into `~/Downloads`

All emit only on transition — steady-state is silent.

## Configuration

Config is created automatically by the setup script. Edit `config.toml` to customize:

```bash
$EDITOR config.toml   # gitignored, per-machine
```

Config auto-discovered: `~/.tokenpal/config.toml` > project root > cwd.

<details>
<summary>Key settings</summary>

```toml
[llm]
api_url = "http://localhost:11434/v1"  # or remote server
model_name = "gemma4"                  # fallback when a server has no remembered model
max_tokens = 60                        # observation cap; auto-raised from server context_length on connect
disable_reasoning = true               # fast responses

# Populated automatically by /model <name> on each server. Hand-edit
# only if you want to pre-seed a machine. Keys are canonical api_urls.
[llm.per_server_models]
# "http://localhost:11434/v1" = "gemma4"
# "http://gpu-box:8585/v1"   = "gemma4:26b-a4b-it-q8_0"

[llm.per_server_max_tokens]
# "http://gpu-box:8585/v1" = 256

[senses]
# These are on by default:
app_awareness = true
hardware = true
idle = true
time_awareness = true
music = true                           # detect Music.app/Spotify (macOS)
productivity = true                    # work patterns from session data
weather = false                        # opt-in: use /zip or first-run wizard
git = false                            # opt-in: reacts to commits and branch switches
battery = false                        # opt-in: plug/unplug + low-battery transitions
network_state = false                  # opt-in: online/offline, wifi changes, VPN
process_heat = false                   # opt-in: names the top CPU hog when pinned
typing_cadence = false                 # opt-in: WPM bursts, post-burst silence (counts keypresses only)
filesystem_pulse = false               # opt-in: activity bursts in watched dirs

# [network_state]
# ssid_labels = { "abcd1234abcd1234" = "home", "ffff0000ffff0000" = "coffee shop" }
# Populate via /wifi label <name> from inside the app — raw SSIDs never stored.

# [filesystem_pulse]
# roots = ["/Users/you/projects/tokenpal"]
# Populate via /watch add <path>. Empty = watch ~/Downloads, ~/Desktop, ~/Documents.

[brain]
comment_cooldown_s = 30.0
active_voice = ""                      # e.g. "bmo"

[conversation]
max_turns = 10                         # turn pairs in history (bump for larger models)
timeout_s = 120                        # seconds of silence before session expires

[actions]
enabled = true

[server]
# host = "0.0.0.0"   # server-side: bind for LAN access
# port = 8585
```

</details>

## Architecture

```
                    ┌─────────┐
User Input ──────▶  │  Brain  │ ──▶ Overlay (ASCII art + speech bubbles)
Senses ──────────▶  │         │
                    └────┬────┘
                    LLM Backend ◀──▶ Actions (tools)
                         │
              ┌──────────┴──────────┐
         Local Ollama        TokenPal Server
                             (remote GPU box)
```

Everything is pluggable via decorators (`@register_sense`, `@register_backend`, `@register_action`). Adding a new sense or action requires zero changes to core code.

<details>
<summary>Project structure</summary>

```
tokenpal/
├── actions/         # LLM-callable tools — defaults + local/utilities/focus/research
│   ├── base.py          # AbstractAction + RateLimit
│   ├── catalog.py       # sections + kind discriminator for /tools describe
│   ├── invoker.py       # shared call-site (rate-limit + usage hook)
│   ├── registry.py      # @register_action discovery
│   └── {focus,network,research,utilities}/
├── brain/           # Orchestrator, context builder, personality, memory
│   ├── agent.py         # /agent multi-step loop + in-run cache
│   ├── research.py      # /research plan-search-read-synthesize pipeline
│   ├── stop_reason.py   # AgentStopReason + ResearchStopReason
├── config/          # TOML schema, loader, weather config helpers
├── llm/             # HTTP backend with auto-fallback (local ↔ remote)
├── senses/          # App awareness, hardware, idle, time, weather, music, productivity, git, battery, network_state, process_heat, typing_cadence, filesystem_pulse
├── server/          # FastAPI inference proxy + training API
├── tools/           # Voice training, LoRA fine-tuning, wiki fetch
├── ui/              # Textual TUI overlay (default), console + tkinter fallbacks
├── util/            # Shared utilities
├── commands.py      # Slash command dispatcher
├── cli.py           # --check, --validate, --verbose, --config, --skip-welcome
├── first_run.py     # First-run welcome wizard
└── app.py           # Bootstrap and main loop

install.sh              # One-liner bootstrap (macOS/Linux) — curl | bash
install.ps1             # One-liner bootstrap (Windows) — iwr | iex

scripts/
├── install-macos.sh    # macOS installer (Python, Ollama, deps, client/server/both)
├── install-windows.ps1 # Windows installer (Python, Ollama, deps, client/server/both)
├── install-linux.sh    # Linux installer (Python, Ollama, deps, client/server/both)
└── start-server.bat    # Start Ollama + server (Windows)

docs/
├── agents-and-tools.md          # /agent, /research, /tools, consent, rate limits, caches
├── server-setup.md              # Server setup guide
├── remote-training-guide.md     # LoRA fine-tuning guide
├── voice-training.md            # Persona cards, anchor lines, ASCII art
├── dynamic-mood-transitions.md  # V2 mood system design (parked)
├── dev-setup-macos.md           # macOS dev environment
├── dev-setup-linux.md           # Linux dev environment
├── dev-setup-windows-*.md       # Windows dev environments (Intel, AMD laptop, AMD desktop)
└── fine-tuning-plan.md          # Fine-tuning architecture notes
```

</details>

## CLI

```
tokenpal                  # run the buddy (first-run wizard on fresh install)
tokenpal --check          # quick: verify Ollama, model, senses, actions
tokenpal --validate       # full: Python, platform deps, git, Ollama, config, permissions
tokenpal --verbose        # debug logs in terminal
tokenpal --config PATH    # specific config file
tokenpal --skip-welcome   # bypass first-run wizard
tokenpal-server           # run the inference server
tokenpal-finetune         # LoRA fine-tuning CLI
```

## Development

```bash
pip install -e ".[macos,dev]"    # macOS
pip install -e ".[windows,dev]"  # Windows
pip install -e ".[server,dev]"   # server extras

pytest                  # tests
ruff check tokenpal/    # lint
tail -f ~/.tokenpal/logs/tokenpal.log  # debug
```

## Privacy

- No clipboard monitoring, no screen content capture
- Goes silent around banking, passwords, health apps
- Browser window titles sanitized (stripped unless music player detected)
- Session memory stores only app names and timestamps, never content
- Conversation history is ephemeral (in-memory only, cleared after ~2 min silence or on sensitive app detection)
- Log files restricted to owner-only (0o600)
- Local by default — LAN GPU server is the main optional network path
- Cloud LLM (Anthropic) is **opt-in** and scoped to `/research` only — never observations, conversation, or any other path. Key at `~/.tokenpal/.secrets.json` (0o600), fingerprint-only in status output. Toggle anytime via `/cloud disable` or `/cloud forget`
- Other opt-in network senses/commands (all free/keyless by default): weather (Open-Meteo), world_awareness (HN Algolia), `/ask` (DuckDuckGo + Wikipedia). Untrusted external text is wrapped in delimiters and content-filtered before reaching the prompt
