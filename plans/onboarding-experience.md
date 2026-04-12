# Onboarding Experience

## Goal
Make "git clone → first quip" smooth and delightful. Fix setup script modes (#9), correct misleading config defaults, and add a first-run welcome flow so new users discover features without reading docs.

## Non-goals
- Rewriting the overlay/UI layer
- Adding new senses or actions
- Fine-tuning pipeline changes (#4, #5)
- Conversation history (#8) or status bar (#7) — those are feature work, not onboarding
- Windows-specific setup (PowerShell launcher, winget flow) — defer to target-machine sessions

## Files to touch
- `setup_tokenpal.py` — add `--local` / `--client` flags, interactive config step
- `config.default.toml` — fix misleading defaults (disable phantom senses, enable real ones)
- `tokenpal/cli.py` — detect first run, trigger welcome flow
- `tokenpal/app.py` — wire first-run check before main loop
- `tokenpal/first_run.py` (new) — welcome wizard: explain features, prompt for weather zip, suggest `/voice`, `/help`
- `tokenpal/config/loader.py` — may need a "write back to config.toml" helper for first-run choices
- `tests/test_first_run.py` (new) — unit tests for first-run logic

## Plan

### Phase 1: Fix config defaults
Correct `config.default.toml` so it reflects reality:
- **Disable phantom senses**: `screen_capture = false`, `clipboard = false`, `ocr = false`, `vision = false`, `voice = false`, `web_search = false` — these have no implementation, shouldn't be `true`
- **Enable shipped senses**: `music = true`, `productivity = true` — these work and are interesting. Weather stays `false` (needs zip code, first-run will prompt)
- Update `--check` output if any sense is enabled but has no platform impl (nice warning instead of silent skip)

### Phase 2: Setup script modes (`--local` / `--client`)
Add argparse to `setup_tokenpal.py`:
- **`--client`**: skip Ollama install + model download, prompt for remote server URL, write `[server]` section to config.toml
- **`--local`**: full install (current behavior + HF token prompt for gated models)
- **Default (no flag)**: current behavior unchanged — detect Ollama, prompt to install/pull
- All three paths converge at config setup + verify + summary

### Phase 3: First-run welcome flow
Detect first run (no `~/.tokenpal/` dir, or a `.first_run_done` marker file).
When triggered, print a short interactive welcome in the console:

```
┌──────────────────────────────────────┐
│  Welcome to TokenPal!                │
│  Your sarcastic AI desktop buddy.    │
└──────────────────────────────────────│

Let's get you set up:

1. Weather — enter a zip code for weather-aware commentary
   > [skip / zip code]

2. Voice — TokenPal can impersonate characters (Bender, GLaDOS, etc.)
   Run /voice list to browse, or /voice train to create your own.

3. Useful commands:
   /help    — see all commands
   /mood    — check TokenPal's mood
   /status  — see active senses
   /model   — change the LLM model

Ready! TokenPal is now watching. Type anything to chat.
```

- Weather prompt: if user enters a zip, geocode + write to config (reuse `/zip` logic)
- Write `~/.tokenpal/.first_run_done` marker on completion
- `--skip-welcome` CLI flag to bypass (for scripting/CI)
- Keep it under 30 seconds — no walls of text

### Phase 4: Setup script summary polish
- Replace `python -m tokenpal` with `tokenpal` in the summary
- Add hint about `tokenpal --check` to verify everything
- Add hint about first-run wizard

## Failure modes to anticipate
- Config write-back from first-run could clobber user's hand-edited config.toml — need merge-style write, not overwrite
- `screen_capture = true` → `false` default change could surprise existing users who already copied config.default.toml — but config.toml is gitignored and per-machine, so only fresh installs are affected
- Weather geocoding in first-run needs network — handle offline gracefully (skip with message)
- `--client` mode needs to validate the server URL is reachable before writing config
- First-run detection via marker file: what if user deletes `~/.tokenpal/` to reset? That's actually fine — they'd get the wizard again, which is the right behavior
- `music = true` on non-macOS: sense should gracefully no-op (verify this)
- Interactive prompts in setup_tokenpal.py won't work in piped/CI contexts — detect `sys.stdin.isatty()`

## Done criteria
- `python3 setup_tokenpal.py --client` completes without Ollama, writes server URL to config
- `python3 setup_tokenpal.py --local` works identically to current default behavior
- `python3 setup_tokenpal.py` (no flag) unchanged behavior
- Fresh install with no `~/.tokenpal/` triggers welcome wizard on first `tokenpal` run
- Welcome wizard lets user set zip code and writes it to config.toml
- `config.default.toml` has no `true` flags for unimplemented senses
- `music` and `productivity` default to `true`
- `tokenpal --check` warns if an enabled sense has no platform implementation
- All existing tests pass, new tests cover first-run logic
- `ruff check` and `mypy` clean

## Parking lot
(empty)
