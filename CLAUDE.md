# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates sarcastic commentary using a local LLM.

## Architecture
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Threading: async brain loop in daemon thread, sync UI on main thread. Communication via `overlay.schedule_callback()`
- Senses produce `SenseReading` with natural-language `.summary` (NOT bracketed tags — LLMs echo those back)
- Per-sense polling intervals via `ClassVar[float]` on `AbstractSense` (e.g., hardware 10s, time 30s, idle 1s)
- Interestingness scoring: per-sense weights in `context.py`, read-only scoring + explicit `acknowledge()` after comment
- Session memory: SQLite at `~/.tokenpal/memory.db`, records app switches + idle returns, injects history into prompts

## Key Commands
- `pip install -e ".[macos,dev]"` — install with macOS extras
- `python -m tokenpal` — run the buddy
- `ollama serve` / `brew services start ollama` — LLM backend must be running
- `tail -f ~/.tokenpal/logs/tokenpal.log` — debug log (DEBUG level, rotated at 5MB)

## Senses
- `app_awareness` (macOS: Quartz window list, NOT NSWorkspace — NSWorkspace is unreliable from background threads)
- `hardware` (psutil, cross-platform, expressive summaries at high utilization)
- `time_awareness` (cross-platform, session duration tracking)
- `idle` (pynput, cross-platform, three tiers: short/medium/long, transition-only readings)

## Brain
- `PersonalityEngine`: rotating few-shot examples (25 pool, sample 5-7), comment history deque, structure hints, mood system (6 moods), running gags (dynamic app detection), guardrails (sensitive apps, compliment ratio, late-night tone)
- `ContextWindowBuilder`: per-sense weighted interestingness, acknowledge pattern prevents consumed changes
- Easter eggs bypass LLM (3:33 AM, Friday 5 PM, Zoom → "Condolences.", Calculator → "Math. Voluntarily.")
- Graceful degradation: confused quips when Ollama is unreachable
- Session memory: `MemoryStore` in `tokenpal/brain/memory.py`, cross-session app visit counts + history lines in prompt

## UI
- Console overlay: bottom-anchored layout, typing animation (30ms/char), live status bar (mood + senses + last spoke)
- Buddy art: block-character chonky design with Cyrillic eyes, 4 expressions (idle/talking/thinking/surprised)
- Tkinter overlay: alternative, always-on-top window (less reliable than console)

## LLM Notes
- Default model: `gemma3:4b` via Ollama. Fast (<1s), decent at short quips.
- Qwen3 models use internal `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- `max_tokens: 40` keeps responses tight. Response filter: strips asterisks, keeps 1 sentence, drops >70 chars.
- Prompt template in `personality.py` has mood line, structure hint, examples, session notes, memory block, context, recent comments.

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent` (causes text overlap)
- macOS: app awareness uses Quartz `CGWindowListCopyWindowInfo` (NOT `NSWorkspace.frontmostApplication()`)
- Console overlay (`overlay = "console"`) is default and more reliable than tkinter
- `config.toml` is gitignored — each machine has its own (different model, backend, senses)

## Privacy
- No clipboard monitoring (explicitly rejected — privacy liability)
- Sensitive app exclusion list: banking, password managers, health apps, messaging — goes silent
- Session memory stores only app names and timestamps, never content/URLs/keystrokes
- SQLite db at `~/.tokenpal/memory.db` with 0o600 permissions
- Browser content guardrails deferred — needs tab title/URL awareness

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides: `docs/dev-setup-macos.md`, `docs/dev-setup-windows-intel.md`, `docs/dev-setup-windows-amd.md`, `docs/dev-setup-windows-amd-desktop.md`
- Brainstorm docs: `plans/next-batch-*.md` (historical — most content now shipped)

## What's Left
- Better ASCII art (user designing their own)
- Daily summaries in SQLite (end-of-day aggregation)
- MLX backend (native macOS inference, skip Ollama)
- Music detection sense
- Windows app_awareness impl (macOS-only right now)
- Browser content guardrails (needs tab title/URL filtering)
- Security review (pre-release audit of input listeners + privacy surface)
