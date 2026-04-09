# TokenPal

Cross-platform AI desktop buddy. ASCII character observes your screen via modular "senses" and generates sarcastic commentary using a local LLM.

## Architecture
- Plugin discovery: `@register_sense` / `@register_backend` / `@register_overlay` decorators + `pkgutil.walk_packages`
- Config: TOML (`config.default.toml` defaults, `config.toml` user overrides gitignored) → dataclass schema in `tokenpal/config/schema.py`
- Threading: async brain loop in daemon thread, sync UI on main thread. Communication via `overlay.schedule_callback()`
- Senses produce `SenseReading` with natural-language `.summary` (NOT bracketed tags — LLMs echo those back)

## Key Commands
- `pip install -e ".[macos,dev]"` — install with macOS extras
- `python -m tokenpal` — run the buddy
- `ollama serve` / `brew services start ollama` — LLM backend must be running
- `brew install python-tk@3.14` — tkinter for macOS (match Python version)

## LLM Notes
- Default model: `gemma3:4b` via Ollama. Fast (<1s), decent at short quips.
- Qwen3 models use internal `<think>` tags → empty responses via OpenAI-compat API. Don't use.
- Persona prompt needs few-shot examples WITH punchlines or model just states facts.
- Response filter in `personality.py` strips quotes, leaked tags, assistant artifacts.

## Platform Notes
- macOS: use `alpha` transparency on tkinter, NOT `systemTransparent` (causes text overlap)
- Console overlay (`overlay = "console"`) is default and more reliable than tkinter
- `config.toml` is gitignored — each machine has its own (different model, backend, senses)

## Code Style
- Python 3.12+, strict mypy, ruff for linting
- abc.ABC for abstractions, dataclasses for data, ClassVar for registry metadata
- Sense implementations go in `tokenpal/senses/<sense_name>/<platform>_impl.py`

## Repo
- GitHub: github.com/smabe/TokenPal (private)
- 4 target machines: Mac (M-series), Dell XPS 16 (Intel NPU), AMD laptop (RTX 4070), AMD desktop (RX 9070 XT)
- Dev setup guides: `docs/dev-setup-macos.md`, `docs/dev-setup-windows-intel.md`, `docs/dev-setup-windows-amd.md`, `docs/dev-setup-windows-amd-desktop.md`
- Plan file: `.claude/plans/shimmying-whistling-thunder.md` (full architecture + roadmap)
