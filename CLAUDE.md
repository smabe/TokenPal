# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates witty commentary using a local LLM.

## Architecture
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` / `@register_action` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Config loading: location-independent — finds defaults relative to package, searches `~/.tokenpal/config.toml` → project root → cwd
- Threading: async brain loop in daemon thread, sync UI on main thread. Communication via `overlay.schedule_callback()`
- User input: main thread captures keystrokes in cbreak mode, routes via `brain.submit_user_input()` (asyncio.Queue + call_soon_threadsafe)
- Senses produce `SenseReading` with natural-language `.summary` (NOT bracketed tags — LLMs echo those back)
- Per-sense polling intervals via `ClassVar[float]` on `AbstractSense` (e.g., hardware 10s, time 30s, idle 1s)
- Interestingness scoring: per-sense weights in `context.py`, read-only scoring + explicit `acknowledge()` after comment
- Actions: `AbstractAction` with `@register_action`, tool specs sent to LLM via OpenAI-compat tools API, multi-turn execution loop (max 3 rounds)
- Data directory: configurable via `[paths] data_dir` in config (default `~/.tokenpal`), holds logs/, memory.db, voices/
- Session memory: SQLite at `{data_dir}/memory.db`, records app switches + idle returns, injects history into prompts

## Key Commands
- `python3 setup_tokenpal.py` — one-command setup (venv, deps, Ollama, config)
- `./run.sh` or `tokenpal` — run the buddy
- `tokenpal --check` — verify Ollama, model, senses, actions
- `tokenpal --verbose` — show debug logs in terminal
- `tokenpal --config PATH` — use specific config file
- `ollama serve` / `brew services start ollama` — LLM backend must be running
- `pytest` — run tests (135 tests, asyncio_mode=auto)
- `ruff check tokenpal/` — lint (line-length 100, select E/F/I/N/W/UP)
- `mypy tokenpal/ --ignore-missing-imports` — type check (strict mode)
- `tail -f ~/.tokenpal/logs/tokenpal.log` — debug log (DEBUG level, rotated at 5MB)

## Senses
- `app_awareness` (macOS: Quartz window list, NOT NSWorkspace — NSWorkspace is unreliable from background threads)
- `hardware` (psutil, cross-platform, expressive summaries at high utilization)
- `time_awareness` (cross-platform, session duration tracking)
- `idle` (pynput, cross-platform, three tiers: short/medium/long, transition-only readings)

## Brain
- `PersonalityEngine`: rotating few-shot examples (27 pool, sample 5-7), comment history deque, voice-specific structure hints, mood system (6 moods), running gags (dynamic app detection), guardrails (sensitive apps, compliment ratio after 3, late-night tone)
- Three prompt paths: `build_prompt()` for observations, `build_freeform_prompt()` for unprompted thoughts (no screen context, rich voices only), `build_conversation_prompt()` for user input
- `_apply_voice()` consolidates all voice field init; `_sample_examples()` and `_pick_hint()` shared across prompt builders
- Freeform thoughts: `has_rich_voice` (50+ lines), `_should_freeform()` (15% chance, 45s min gap), `_generate_freeform_comment()`
- `_emit_comment()` consolidates comment bookkeeping (record, show, timestamps) across observation/freeform/easter egg paths
- Shared `_clean_llm_text()` with pre-compiled regex constants for response cleanup (includes orphan punctuation stripping)
- `ContextWindowBuilder`: per-sense weighted interestingness, acknowledge pattern prevents consumed changes
- Easter eggs bypass LLM (3:33 AM, Friday 5 PM, Zoom → "Condolences.", Calculator → "Math. Voluntarily.")
- Graceful degradation: confused quips when Ollama is unreachable, "Ollama unreachable" pushed to status bar on first failure
- Session memory: `MemoryStore` in `tokenpal/brain/memory.py`, cross-session app visit counts + history lines in prompt
- User input: `submit_user_input()` (thread-safe), `_handle_user_input()` (async, max_tokens=100)

## Actions
- `tokenpal/actions/` package with `@register_action` decorator pattern
- `AbstractAction`: `action_name`, `description`, `parameters` (JSON Schema), `execute(**kwargs) -> ActionResult`, `teardown()`
- Built-in: `timer` (countdown, 1hr cap, 5 concurrent max), `system_info` (psutil stats), `open_app` (safety-allowlisted)
- Tool specs sent to LLM via Ollama's OpenAI-compat `/v1/chat/completions` with `tools` parameter
- Multi-turn execution: Brain loops up to 3 rounds, parallel tool calls via `asyncio.gather()`
- `ActionsConfig` in schema with per-action enable flags

## Slash Commands
- `tokenpal/commands.py`: `CommandDispatcher` with `CommandResult` dataclass
- Built-in: `/help`, `/clear`, `/mood`, `/status`, `/model [name|list|pull|browse]`, `/voice [list|switch|off|info|train]`
- `/model` shows current, `/model <name>` swaps, `/model list` shows installed, `/model pull <name>` downloads, `/model browse` shows recommended
- `/voice train <wiki> "<character>"` — background thread, progress callbacks, pauses brain during training
- `/voice finetune <name>` — remote LoRA fine-tuning (see Fine-Tuning section below)
- `/voice finetune-setup` — one-time remote GPU environment setup
- Dispatched from main thread, results shown as speech bubbles

## UI
- Console overlay: bottom-anchored layout, typing animation (30ms/char), live status bar (mood + senses + last spoke), `SpeechBubble.persistent` for training progress
- `brain.paused` suppresses comments during voice training
- Text input: cbreak mode (termios), non-blocking stdin via `select.select()`, dirty-flag coalesced redraws
- Input line rendered between bottom border and status bar: `> typed text here_`
- Buddy art: block-character chonky design with Cyrillic eyes, 4 expressions (idle/talking/thinking/surprised)
- Tkinter overlay: alternative, always-on-top window (less reliable than console)

## LLM Notes
- Default model: `gemma4` via Ollama. Supports tool calling.
- `disable_reasoning: true` (default) sends `reasoning_effort: "none"` to skip internal thinking. Without this, gemma4 burns ~900 tokens thinking before responding, making it slow and often returning empty content.
- Qwen3 models use `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- Response filter: strips asterisks, leaked tags, prefixes, orphan punctuation. Observation: 1-2 sentences, min 15 chars. Conversation: 2 sentences, 5-150 chars.
- Tool calling: Ollama's OpenAI-compat API with `tools` parameter. `tool_choice` not supported (model decides). Arguments come as JSON strings.
- `AbstractLLMBackend.set_model()` for runtime model swap. `model_name` property exposed on base class.
- Prompt template in `personality.py` has mood line, structure hint, examples, session notes, memory block, context, recent comments.

## Voice Training
- `tokenpal/tools/train_voice.py`: `_generate_voice_assets()` runs 5 parallel Ollama calls (persona, greetings, offline quips, mood prompts, structure hints)
- `tokenpal/tools/wiki_fetch.py`: `_strip_wiki_markup()` normalizes all Fandom wiki formats, then `_wikitext_to_dialogue()` extracts `Name: dialogue` lines. Handles `{{L|Name|dialogue}}` templates and `'''Name:'''` bold formats
- `tokenpal/tools/voice_profile.py`: `VoiceProfile` dataclass with lines, persona, greetings, offline_quips, mood_prompts, structure_hints
- `train_from_wiki()` accepts `progress_callback` for live UI updates during training
- Profiles saved to `~/.tokenpal/voices/<slug>.json`, auto-activated in config.toml

## Fine-Tuning
- Remote LoRA fine-tuning via SSH to a GPU box (RTX 4070 tested end-to-end with Gemma-2 2B IT + BMO). Two paths: native Windows (recommended for Windows hosts) and Linux/WSL. ROCm pipeline works for RDNA 3; RDNA 4 / gfx1201 (RX 9070 XT) blocked by ROCm 7.2 kernel dispatch — detected but can't run compute, waiting on ROCm 7.3+
- Stack: PEFT + bitsandbytes (QLoRA on Linux CUDA) or bf16 full-precision LoRA (on ROCm, Windows, since bitsandbytes is unreliable/unavailable) + TRL `SFTConfig`/`SFTTrainer`, merged to safetensors (not GGUF)
- **Recommended model**: `google/gemma-2-2b-it` — strong for size, fits 8GB VRAM (~7.9GB peak on Windows bf16, ~7.1GB on Linux QLoRA), ~15 min training on Windows RTX 4070, ~7 min on Linux
- **Pinned deps**: `transformers==4.56.1` (4.57.2 has a bug with local model paths), `remove_columns=["conversations"]` when mapping dataset to prevent TRL from re-applying chat template
- **Gemma-2 note**: does not support system role in chat template
- `tokenpal/tools/remote_train.py`: SSH/SCP orchestrator, wheel bundle builder, platform-aware (Linux tmux / Windows PowerShell)
- `tokenpal/tools/finetune_voice.py`: standalone CLI (`tokenpal-finetune`) with subcommands: prep, train, merge, export, register, all. `_count_lines()` helper for JSONL line counting. `_is_windows()` routes Windows to bf16 LoRA (no QLoRA)
- `tokenpal/tools/dataset_prep.py`: voice lines → ShareGPT-format JSONL (observation 75%, conversation 15%, freeform 10%)
- Config: `[finetune]` (base_model, lora_rank, epochs, batch_size) + `[finetune.remote]` (host, user, port, use_wsl, gpu_backend, platform)
- `platform: str = "auto"` in `RemoteTrainConfig` — auto-detects via SSH probe (`uname -s 2>/dev/null || ver`), or set explicitly to `"linux"` / `"windows"`
- Base models tested: TinyLlama 1.1B (works, garbled output). Recommended: Gemma-2 2B IT (gated but strong, fits 8GB VRAM). Gemma-2 9B for best quality (needs HF token + more VRAM)
- Pipeline: build wheel → push bundle → install.sh/install.ps1 → push base model → prep data → train (tmux on Linux, synchronous SSH on Windows) → merge adapter → pull safetensors → register Ollama
- Wheel bundle: auto-built in `remote_finetune()`, hash-compared (`_hash_training_sources()`), only re-pushed when training code changes. Bundle includes both install.sh and install.ps1 so it works on any platform
- **Linux installer** (`install.sh`, embedded as `_INSTALL_SH`): WSL `/mnt/c/` self-relocation, Python 3.12+ check, CUDA/ROCm/Intel NPU detection, PyTorch index URL selection (ROCm version aware: 7.2 vs 6.2), HSA env var exports, skips PyTorch download if already installed
- **Windows installer** (`install.ps1`, embedded as `_INSTALL_PS1`): Python `py` launcher check, CUDA-only (no ROCm on Windows), auto-detects CUDA version from nvidia-smi for PyTorch index URL (cu126/cu128/cu130). Phase order: training extras first, CUDA torch last (transitive deps pull CPU torch from PyPI otherwise). Uninstalls triton after torch install (broken Windows binaries). UTF-8 BOM required for PS 5.1 (without BOM, reads as Windows-1252, em dashes become quote chars)
- **Windows training runner** (`run_train.ps1`, embedded as `_TRAIN_PS1`): parameterized, sets `TORCHDYNAMO_DISABLE=1` (triton import crashes), `HF_HUB_OFFLINE=1`, pipes to `Tee-Object` for real-time progress. `$LASTEXITCODE` is reliable through `Tee-Object` pipelines (tested)
- Training: Linux runs in `tmux` session (survives SSH drops), polled every 30s. Windows runs synchronously in the SSH session (no tmux, documented non-goal). Both tee output to `train.log`
- Checkpoint resume: `--resume` flag auto-detected from existing `checkpoint-*` dirs, passed to HF Trainer
- Concurrent training: `flock` on Linux, skipped on Windows (no equivalent, documented non-goal)
- Merge: `merge_adapter()` loads base + LoRA adapter via PEFT, saves merged safetensors. Ollama registers via `FROM ./merged` Modelfile
- Model pull: rsync for Linux (progress + resume), SCP for Windows/WSL (no rsync). SCP remote paths must use forward slashes (backslash `C:\` breaks the host:path delimiter). SCP `-r` creates a nested subdir — code renames atomically on success
- Model integrity: sha256 of safetensors verified after pull. **Mismatch is a hard error**. Hash computation uses `\n` line separators (not `[Environment]::NewLine` which is `\r\n` on Windows)
- Base model integrity: `_ensure_base_model` (Linux, bash) / `_ensure_base_model_windows` (PowerShell via `_build_windows_base_model_check`): config.json with `model_type` + nonzero weight shards. Windows variant wrapped in `powershell -Command "..."` (SSH default shell is cmd.exe)
- Disk space preflight: warns if < 25GB free on remote. Windows uses `Get-PSDrive`, Linux uses `df -BG`
- Preflight remote state: `_preflight_remote_state` takes `platform` param (no default). Linux: flock/tmux/venv probe in one round-trip. Windows: venv-only (cmd.exe `echo` + python, no flock/tmux)
- All PowerShell command builders avoid `\"` escaping inside `powershell -Command "..."` — cmd.exe misparses `\"` as string terminators. Use single-quoted strings, string concatenation, `[char]10` for newlines
- All file I/O across tools uses `encoding="utf-8"` explicitly — Windows defaults to cp1252 which can't handle Unicode characters (e.g., musical notes in voice profiles)
- HF auth errors detected via `_looks_like_hf_auth_error` heuristic on both remote and local paths. Windows HF_TOKEN via `setx` (persistent) or `$env:HF_TOKEN` (session), Linux via `~/.bashrc`
- Actionable errors: `RemoteTrainError` includes `hint` with platform-appropriate debug commands (`.venv\Scripts\activate` + `type train.log` on Windows, `source .venv/bin/activate` + `cat train.log` on Linux)
- SSH/SCP/rsync: `RemoteTrainConfig` has `port: int = 22` field; `_run_ssh` uses `-p`, `_run_scp` uses `-P`, `_run_rsync` passes `-p` in ssh command. Local `scp.exe` must be in PATH on the controller machine
- Direct WSL SSH (recommended for WSL users): run `openssh-server` inside WSL on port 2222, set `use_wsl = false` + `port = 2222` — treats WSL as native Linux
- WSL-specific (legacy): base64-encoded training scripts (survive SSH→PowerShell→WSL quoting), `_resolve_wsl_mount()` for SCP↔WSL bridge
- See `docs/remote-training-guide.md` for user-facing setup and usage

## Server (Client-Server Architecture)
- `tokenpal/server/` package: FastAPI app for inference proxy + training orchestration
- `tokenpal-server` entry point, `[server]` extras group (fastapi, uvicorn)
- Inference: byte-forwarding `/v1/*` proxy to local Ollama (no JSON deserialization, streaming-ready)
- Training: `POST /api/v1/train {"wiki": "...", "character": "..."}` → server handles full pipeline (wiki fetch → dataset prep → train → merge → register)
- Training worker: `asyncio.to_thread()` wrapping `finetune_voice` functions, `asyncio.Lock` for one-job-at-a-time
- Job state: JSON files at `~/.tokenpal-server/jobs/`, crash recovery on startup (stale job detection)
- Auth: pluggable `AbstractAuth` → `NoAuth` (v1), `SharedSecretAuth` (v2). Via `app.state.auth_backend`
- Default bind: `127.0.0.1` (localhost only). LAN access opt-in via `host = "0.0.0.0"` in config
- Auto-fallback: when configured server unreachable and `mode = "auto"`, HttpBackend tries `localhost:11434/v1`
- Status bar shows server name when connected to remote (`geefourteen | gemma4 | happy`)
- `/server status` and `/server switch local/remote/<host>` slash commands
- Input validation: wiki `^[a-zA-Z0-9-]+$`, character `^[a-zA-Z0-9 _.'-]+$`, model `^[a-zA-Z0-9_.-]+(:[a-zA-Z0-9_.-]+)?$`
- Config: `ServerConfig` in schema.py with `Literal` types for `mode` and `auth_backend`
- Installers: `scripts/install-server.sh` (Linux/macOS, systemd user unit) + `scripts/install-server.ps1` (Windows, startup shortcut)
- Training worker unloads Ollama models (`keep_alive: 0` via `/api/ps` + `/api/generate`) before training to free VRAM
- Voice training (`train_voice.py`): `_get_model()` reads from config (not hardcoded), `reasoning_effort: "none"` required for gemma4 (burns tokens on thinking otherwise, returns empty content), 120s timeout for parallel voice asset generation
- Ollama on Windows: not in PATH from cmd.exe SSH — use full path `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. CLI from SSH may try to start a new instance; use PowerShell wrapper or ensure Ollama is already running
- Ollama safetensors crash: `ollama create` panics on Gemma-2's `additional_special_tokens` (string format, expects dict). Workaround: convert to GGUF via `convert_hf_to_gguf.py` (b4921 tag matches gguf 0.18.0)
- Fine-tuned 2B models can't handle tool calling — use gemma4 + voice profiles for daily use, fine-tuned models for experiments
- `OLLAMA_KEEP_ALIVE=1m` in `start-server.bat` — unloads model from VRAM after 1 min idle (frees GPU for gaming)
- Voice persona replaces default TokenPal identity (not appended) — `_identity_block()` in personality.py
- Response filter sentence cap relaxed for voices: observations 3 (was 2), conversations 4 (was 2) — via `_cap_sentences()` helper
- See `docs/server-setup.md` for user-facing setup guide

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent` (causes text overlap)
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace.frontmostApplication()`)
- Console overlay (`overlay = "console"`) is default and more reliable than tkinter
- `config.toml` is gitignored — each machine has its own (different model, backend, senses)

## Privacy
- No clipboard monitoring (explicitly rejected — privacy liability)
- Sensitive app exclusion list: banking, password managers, health apps, messaging — goes silent
- Session memory stores only app names and timestamps, never content/URLs/keystrokes
- SQLite db at `{data_dir}/memory.db` with 0o600 permissions
- Browser content guardrails deferred — needs tab title/URL awareness

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides: `docs/dev-setup-macos.md`, `docs/dev-setup-windows-intel.md`, `docs/dev-setup-windows-amd.md`, `docs/dev-setup-windows-amd-desktop.md`
- Plan files in `plans/` — shipped plans marked `[SHIPPED]`, `npu-buddy-exploration.md` is the original vision doc

## What's Left
- Better ASCII art (user designing their own)
- Daily summaries in SQLite (end-of-day aggregation)
- MLX backend (native macOS inference, skip Ollama)
- Music detection sense
- Windows app_awareness impl (macOS-only right now)
- Windows text input (needs msvcrt instead of termios)
- Browser content guardrails (needs tab title/URL filtering)
- Security review (pre-release audit of input listeners + privacy surface)
