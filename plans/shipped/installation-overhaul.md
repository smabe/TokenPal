# Installation Overhaul

## Goal
Replace the current fragmented setup scripts with unified, platform-specific installers that handle Python installation, dependency management, and interactive feature selection (client / server / both). Also clean up dead deps, add `--validate`, add SETUP.md routing doc, and add Linux client doc.

## Non-goals
- Rewriting the first-run wizard or TUI
- Adding new senses or features
- Changing the config schema
- Lock files / pip-compile (loose pins are fine for a dev-stage project — revisit post-1.0)
- Offline/airgapped installation support

## Phases

### Phase 1: Cleanup pyproject.toml
- Remove `mss`, `pyperclip`, `wmi` from dependencies (dead code — never imported)

### Phase 2: `tokenpal --validate` (expand `--check`)
- Extend `cli.py` to add a `--validate` flag that does a full preflight:
  - Python version
  - Platform deps installed (pyobjc on macOS, pywin32 on Windows)
  - `git` in PATH (for git sense)
  - Ollama reachable + model available (existing --check logic)
  - Config file exists and parses
  - macOS: Accessibility permission hint
- Keep `--check` as-is (alias to existing behavior), `--validate` is the superset

### Phase 3: Platform installers with interactive feature selection
Three new standalone installer scripts that install Python AND all deps:

**`install-macos.sh`** (replaces needing setup_tokenpal.py on Mac)
- Installs Xcode CLI tools if missing
- Installs Homebrew if missing (or skips with warning)
- Installs Python 3.12+ via brew
- Prompts: "Install as: [C]lient only, [S]erver only, [B]oth?" 
- Client: clones repo, venv, pip install with `[macos,dev]`, Ollama + model pull
- Server: adds `[server]` extra, firewall note, launchd plist
- Both: union of above
- Runs `tokenpal --validate` at end

**`install-windows.ps1`** (replaces bootstrap.ps1 + install-server.ps1 + setup_tokenpal.py on Windows)
- Installs Python 3.12 via winget
- Installs Git via winget
- Prompts: "Install as: [C]lient only, [S]erver only, [B]oth?"
- Client: clones repo, venv, pip install with `[windows,dev]`, Ollama via winget + model pull
- Server: adds `[server]` extra, firewall rule, startup bat + optional shortcut
- Both: union of above
- Runs `tokenpal --validate` at end

**`install-linux.sh`** (replaces bootstrap.sh + install-server.sh + setup_tokenpal.py on Linux)
- Detects package manager (apt/dnf/pacman/zypper)
- Installs Python 3.12+, git, build-essential
- Prompts: "Install as: [C]lient only, [S]erver only, [B]oth?"
- Client: clones repo, venv, pip install with `[dev]`, Ollama + model pull
- Server: adds `[server]` extra, firewall, systemd unit, HF token
- Both: union of above
- Runs `tokenpal --validate` at end

Each installer is **standalone** — user can curl|bash (or iwr|powershell) from a bare machine. They absorb the logic from the existing scripts so we don't duplicate.

### Phase 4: Update setup_tokenpal.py
- Add model pull offer in `--default` mode (not just `--local`)
- Point users to platform installers for fresh machine setup
- Keep it as the "already have Python" lightweight path

### Phase 5: SETUP.md
- Decision tree: "What OS?" → "Fresh machine or Python already installed?" → link to right script/doc
- One page, no fluff

### Phase 6: Linux client doc
- `docs/dev-setup-linux.md` — brief guide noting: which senses work (hardware, idle, time, weather, git, productivity), which don't (app_awareness, music), X11/Wayland note for pynput

### Phase 7: Deprecation notices on old scripts
- Add header comments to `scripts/bootstrap.sh`, `scripts/bootstrap.ps1`, `scripts/install-server.sh`, `scripts/install-server.ps1` pointing to the new unified installers
- Keep them working (don't break existing deployments) but mark as legacy

## Files to touch
- `pyproject.toml` — remove dead deps
- `tokenpal/cli.py` — add `--validate`
- `scripts/install-macos.sh` — NEW
- `scripts/install-windows.ps1` — NEW
- `scripts/install-linux.sh` — NEW
- `setup_tokenpal.py` — add model pull in default mode, add pointer to platform installers
- `SETUP.md` — NEW (project root)
- `docs/dev-setup-linux.md` — NEW
- `scripts/bootstrap.sh` — deprecation header
- `scripts/bootstrap.ps1` — deprecation header
- `scripts/install-server.sh` — deprecation header
- `scripts/install-server.ps1` — deprecation header

## Failure modes to anticipate
- macOS: Homebrew install requires Xcode CLI tools first — must sequence correctly
- Windows: winget may not be available on older Win10 — need fallback instructions
- Windows: `py` launcher may not be in PATH after fresh Python install — need terminal restart warning
- Linux: pacman/zypper users get "unsupported" from current scripts — new script must handle them
- Ollama `serve` race condition: starting in background then immediately pulling model can fail — need health check loop
- Model pull interruption: no resume support in Ollama — just retry
- Interactive prompts don't work when piped (curl|bash) — need `-y` / `--noninteractive` default
- Firewall rules on Windows need admin elevation — graceful fallback to manual instructions
- Existing users re-running installer shouldn't break their setup — idempotent checks everywhere

## Done criteria
- `pyproject.toml` has no dead deps (mss, pyperclip, wmi removed)
- `tokenpal --validate` runs a full preflight and reports platform-specific issues
- Each platform installer works standalone: installs Python, asks client/server/both, installs everything
- `SETUP.md` exists at project root with clear routing
- `docs/dev-setup-linux.md` exists with sense support matrix
- Old scripts have deprecation headers pointing to new installers
- `setup_tokenpal.py` offers model pull in default mode

## Parking lot
