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
- Remote LoRA fine-tuning via SSH to a GPU box (RTX 4070 tested end-to-end with Gemma-2 2B IT + BMO, ROCm detected but not yet validated)
- Stack: PEFT + bitsandbytes (QLoRA) + TRL `SFTConfig`/`SFTTrainer` (bf16), merged to safetensors (not GGUF)
- **Recommended model**: `google/gemma-2-2b-it` — strong for size, fits 8GB VRAM (~7.1GB used), ~5-10 min training on RTX 4070
- **Pinned deps**: `transformers==4.56.1` (4.57.2 has a bug with local model paths), `remove_columns=["conversations"]` when mapping dataset to prevent TRL from re-applying chat template
- **Gemma-2 note**: does not support system role in chat template
- `tokenpal/tools/remote_train.py`: SSH/SCP orchestrator, wheel bundle builder, tmux training wrapper
- `tokenpal/tools/finetune_voice.py`: standalone CLI (`tokenpal-finetune`) with subcommands: prep, train, merge, export, register, all. `export_gguf()` delegates merge to `merge_adapter()`
- `tokenpal/tools/dataset_prep.py`: voice lines → ShareGPT-format JSONL (observation 75%, conversation 15%, freeform 10%)
- Config: `[finetune]` (base_model, lora_rank, epochs, batch_size) + `[finetune.remote]` (host, user, port, use_wsl, gpu_backend)
- Base models tested: TinyLlama 1.1B (works, garbled output). Recommended: Gemma-2 2B IT (gated but strong, fits 8GB VRAM). Gemma-2 9B for best quality (needs HF token + more VRAM)
- Pipeline: build wheel → push bundle → install.sh → push base model → prep data → train (tmux) → merge adapter → pull safetensors → register Ollama
- Wheel bundle: auto-built in `remote_finetune()`, hash-compared (`_hash_training_sources()`), only re-pushed when training code changes. `--force-reinstall --no-cache-dir` ensures fresh code lands even without version bump
- `install.sh` (embedded as `_INSTALL_SH`): WSL `/mnt/c/` self-relocation, Python 3.12+ check, CUDA/ROCm/Intel NPU detection, PyTorch index URL selection, sentinel file (`.install-ok`) for partial-install recovery, skips PyTorch download if already installed
- Training runs in `tmux` session (survives SSH drops), polled every 30s, output tee'd to `train.log`
- Checkpoint resume: `--resume` flag auto-detected from existing `checkpoint-*` dirs, passed to HF Trainer
- `flock /tmp/tokenpal-training.lock` prevents concurrent training
- Merge: `merge_adapter()` loads base + LoRA adapter via PEFT, saves merged safetensors. Ollama registers via `FROM ./merged` Modelfile
- Model pull: rsync with `--info=progress2` + `--partial` for Linux hosts (progress + resume), SCP `-r` for WSL hosts (no rsync on Windows)
- Model integrity: sha256 of safetensors verified after pull
- Disk space preflight: warns if < 25GB free on remote
- Actionable errors: `RemoteTrainError` includes `hint` with SSH debug commands, checkpoint location, retry instructions
- Shared helpers: `_drain_stream()` (async stream reading), `_resolve_wsl_mount()` (WSL path resolution)
- SSH/SCP/rsync: `RemoteTrainConfig` has `port: int = 22` field; `_run_ssh` uses `-p`, `_run_scp` uses `-P`, `_run_rsync` passes `-p` in ssh command
- Direct WSL SSH (recommended): run `openssh-server` inside WSL on port 2222, set `use_wsl = false` + `port = 2222` — treats WSL as native Linux, eliminates Windows path resolution, PowerShell quoting, and `/mnt/c/` copies. Needs Windows firewall rule for port 2222 and `/usr/lib/wsl/lib` in PATH (via `/etc/environment`)
- WSL-specific (legacy): base64-encoded training scripts (survive SSH→PowerShell→WSL quoting), `_resolve_wsl_mount()` for SCP↔WSL bridge
- Model validation: `_ensure_base_model` checks `config.json` has `model_type` field via grep (not just directory existence) to catch stale/incomplete downloads
- HF_TOKEN: `source ~/.bashrc` before download commands to pick up token. Most models are gated (Llama 3.2, Gemma-2) — need HF token + license acceptance on huggingface.co
- See `docs/remote-training-guide.md` for user-facing setup and usage

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
