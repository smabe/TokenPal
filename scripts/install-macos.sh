#!/usr/bin/env bash
set -euo pipefail

# TokenPal macOS Installer — standalone installer for bare-machine-to-running setup.
# Handles: Xcode CLI tools, Homebrew check, Python 3.12+, repo clone, venv, deps,
# Ollama, model pull, server config (optional), launchd plist, config, validation.
#
# Usage:
#   bash scripts/install-macos.sh                  # interactive prompt
#   bash scripts/install-macos.sh --mode client    # skip prompt, client only
#   bash scripts/install-macos.sh --mode server    # skip prompt, server only
#   bash scripts/install-macos.sh --mode both      # skip prompt, full install
#
# Environment variables:
#   TOKENPAL_DIR    — install directory (default: $HOME/tokenpal)
#   TOKENPAL_MODEL  — Ollama model to pull (default: gemma4)

# ── Globals ─────────────────────────────────────────────────────────────────

TOKENPAL_DIR="${TOKENPAL_DIR:-$HOME/tokenpal}"
MODEL="${TOKENPAL_MODEL:-gemma4}"
MODE=""

# ── Argument parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            shift
            case "${1:-}" in
                client|server|both) MODE="$1" ;;
                *) echo "ERROR: --mode must be client, server, or both"; exit 1 ;;
            esac
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--mode client|server|both]"
            exit 1
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

total_phases=0  # set after mode is known

info() {
    echo "  $1"
}

ok() {
    echo "  OK: $1"
}

warn() {
    echo "  WARNING: $1"
}

fail() {
    echo "  ERROR: $1"
    exit 1
}

# ── Pre-flight: macOS only ──────────────────────────────────────────────────

if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: This installer is for macOS only."
    echo "See scripts/install-linux.sh for Linux."
    exit 1
fi

echo "=== TokenPal macOS Installer ==="
echo ""

# ── Phase 1: Xcode CLI Tools ───────────────────────────────────────────────

# Phase counting happens before we know the mode, so we use a placeholder
# and print phase numbers once we reach each step.

echo "[1/...] Checking Xcode Command Line Tools..."
if xcode-select -p &>/dev/null; then
    ok "Xcode CLI tools installed ($(xcode-select -p))"
else
    info "Xcode CLI tools not found. Installing..."
    info "A system dialog will appear. Click 'Install' and wait for it to finish."
    xcode-select --install 2>/dev/null || true
    # Wait for the user to complete the GUI installation dialog
    echo ""
    info "Waiting for Xcode CLI tools installation to complete..."
    info "Press Enter once the installation dialog finishes."
    read -r < /dev/tty || true
    # Verify it worked
    if xcode-select -p &>/dev/null; then
        ok "Xcode CLI tools installed"
    else
        fail "Xcode CLI tools installation did not complete. Please install manually and re-run."
    fi
fi

# ── Phase 2: Homebrew ───────────────────────────────────────────────────────

echo ""
echo "[2/...] Checking Homebrew..."
if command -v brew &>/dev/null; then
    ok "Homebrew found ($(brew --prefix))"
else
    echo ""
    echo "  Homebrew is not installed. TokenPal needs it for Python and Ollama."
    echo ""
    echo "  Install Homebrew by running:"
    echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo ""
    echo "  Then re-run this installer."
    exit 1
fi

# ── Phase 3: Python 3.12+ ──────────────────────────────────────────────────

echo ""
echo "[3/...] Checking Python 3.12+..."
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" &>/dev/null; then
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
        major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
        if (( major == 3 && minor >= 12 )); then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    info "Python 3.12+ not found. Installing via Homebrew..."
    brew install python@3.12
    PYTHON="python3.12"
    if ! command -v "$PYTHON" &>/dev/null; then
        # Homebrew may put it in a non-PATH location
        PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
        if [[ ! -x "$PYTHON" ]]; then
            fail "Python 3.12 installed but not found in PATH. Try: brew link python@3.12"
        fi
    fi
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
ok "Python $PY_VER ($PYTHON)"

# On Apple Silicon, verify we're running native ARM64 (not Rosetta)
if [[ "$(uname -m)" == "arm64" ]]; then
    py_arch=$("$PYTHON" -c "import platform; print(platform.machine())")
    if [[ "$py_arch" == "arm64" ]]; then
        ok "Python is ARM64 native (Apple Silicon)"
    else
        warn "Python is running under Rosetta ($py_arch). Recommend installing native ARM64 Python via Homebrew."
    fi
fi

# ── Phase 4: Git ────────────────────────────────────────────────────────────

echo ""
echo "[4/...] Checking Git..."
if command -v git &>/dev/null; then
    ok "Git $(git --version | head -1)"
else
    fail "Git not found. It should come with Xcode CLI tools. Try: xcode-select --install"
fi

# ── Phase 5: Feature selection ──────────────────────────────────────────────

echo ""
echo "[5/...] Installation mode..."
if [[ -z "$MODE" ]]; then
    if [[ -t 0 ]]; then
        # Interactive — prompt the user
        echo ""
        echo "  How would you like to install TokenPal?"
        echo "    [C] Client only  -- run the buddy on this Mac"
        echo "    [S] Server only  -- serve LLM inference for other machines"
        echo "    [B] Both         -- full installation"
        echo ""
        while true; do
            printf "  Choose [C/s/b]: "
            read -r choice < /dev/tty || choice="c"
            choice="${choice:-c}"
            case "${choice,,}" in
                c|client) MODE="client"; break ;;
                s|server) MODE="server"; break ;;
                b|both)   MODE="both";   break ;;
                *) echo "  Please enter C, S, or B." ;;
            esac
        done
    else
        # Non-interactive (piped input) — default to client
        MODE="client"
        info "Non-interactive mode detected, defaulting to: client"
    fi
fi
ok "Mode: $MODE"

# Determine pip extras based on mode
case "$MODE" in
    client) PIP_EXTRAS="macos,dev" ;;
    server) PIP_EXTRAS="macos,server,dev" ;;
    both)   PIP_EXTRAS="macos,server,dev" ;;
esac

# Set total phase count now that we know the mode
# Phases: xcode(1) brew(2) python(3) git(4) mode(5) repo(6) venv(7) ollama(8) server?(9) config(10) validate(11) summary(12)
if [[ "$MODE" == "client" ]]; then
    total_phases=11
else
    total_phases=12
fi

# ── Phase 6: Clone or update repo ──────────────────────────────────────────

echo ""
echo "[6/$total_phases] Setting up TokenPal repository..."

# Detect if we're already inside a TokenPal repo
ALREADY_IN_REPO=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
    # Running from inside the repo (e.g., scripts/install-macos.sh)
    TOKENPAL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    ALREADY_IN_REPO=true
    ok "Already in TokenPal repo: $TOKENPAL_DIR"
elif [[ -f "$TOKENPAL_DIR/pyproject.toml" ]]; then
    ALREADY_IN_REPO=true
    ok "TokenPal repo exists: $TOKENPAL_DIR"
fi

if [[ "$ALREADY_IN_REPO" == false ]]; then
    if command -v gh &>/dev/null; then
        info "Cloning TokenPal to $TOKENPAL_DIR..."
        gh repo clone smabe/TokenPal "$TOKENPAL_DIR"
    elif command -v git &>/dev/null; then
        info "Cloning TokenPal to $TOKENPAL_DIR..."
        git clone https://github.com/smabe/TokenPal.git "$TOKENPAL_DIR"
    else
        fail "Neither gh nor git found. Cannot clone repo."
    fi
    ok "Cloned to $TOKENPAL_DIR"
else
    # Pull latest if already cloned
    if git -C "$TOKENPAL_DIR" remote -v &>/dev/null 2>&1; then
        info "Pulling latest changes..."
        git -C "$TOKENPAL_DIR" pull --ff-only 2>/dev/null || warn "Could not pull latest (not on a tracking branch or conflicts). Continuing with existing code."
    fi
fi

# ── Phase 7: Venv + pip install ─────────────────────────────────────────────

echo ""
echo "[7/$total_phases] Setting up Python environment..."
VENV_DIR="$TOKENPAL_DIR/.venv"

if [[ -d "$VENV_DIR" ]]; then
    ok "Virtual environment exists: $VENV_DIR"
else
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created $VENV_DIR"
fi

# Activate and install
source "$VENV_DIR/bin/activate"
info "Upgrading pip..."
pip install --upgrade pip -q

info "Installing TokenPal with [$PIP_EXTRAS] extras..."
pip install -e "$TOKENPAL_DIR[$PIP_EXTRAS]" -q
ok "Dependencies installed"

# ── Phase 8: Ollama + model ─────────────────────────────────────────────────

echo ""
echo "[8/$total_phases] Setting up Ollama and model..."

if [[ "$MODE" == "client" ]]; then
    ok "Client mode — skipping local Ollama install (inference happens on remote server)"
    info "If you want a local fallback, install later with: brew install ollama"
else

if ! command -v ollama &>/dev/null; then
    info "Ollama not found. Installing via Homebrew..."
    brew install ollama
fi
ok "Ollama installed"

# Start Ollama if not already running
if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    info "Starting Ollama..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!

    # Health check loop: poll up to 30 seconds
    waited=0
    while (( waited < 30 )); do
        if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
        warn "Ollama did not start within 30s. You may need to start it manually: ollama serve"
    else
        ok "Ollama is running (started in background, PID $OLLAMA_PID)"
    fi
else
    ok "Ollama is already running"
fi

# Recommend model based on available memory (Apple Silicon = unified memory)
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    total_bytes=$(sysctl -n hw.memorysize 2>/dev/null || echo 0)
    total_gb=$(( total_bytes / 1073741824 ))
    # Tiers. For reasoning models (deepseek-r1, qwq), override via TOKENPAL_MODEL.
    # TokenPal strips think tags, so reasoning models are best for /ask not observations.
    # Apple Silicon shares unified memory with the OS, so leave room: the tier
    # thresholds already assume ~8GB of headroom for macOS itself.
    if (( total_gb >= 48 )); then
        RECOMMENDED="llama3.3:70b"
        info "Detected ${total_gb}GB unified memory, recommending llama3.3:70b (70B, best quality)"
    elif (( total_gb >= 32 )); then
        RECOMMENDED="qwen2.5:32b"
        info "Detected ${total_gb}GB unified memory, recommending qwen2.5:32b (32B). gemma4:26b also fits."
    elif (( total_gb >= 24 )); then
        RECOMMENDED="gemma4:26b"
        info "Detected ${total_gb}GB unified memory, recommending gemma4:26b (26B, ~19GB + OS headroom)"
    elif (( total_gb >= 6 )); then
        RECOMMENDED="gemma4"
        info "Detected ${total_gb}GB unified memory, recommending gemma4 (9B, solid default)"
    else
        RECOMMENDED="gemma2:2b"
        info "Detected ${total_gb}GB unified memory, recommending gemma2:2b (2B, fits tight memory)"
    fi

    # Let user confirm or override
    if [[ -t 0 ]] && [[ "$MODEL" == "${TOKENPAL_MODEL:-gemma4}" ]]; then
        printf "  Pull %s? [Y/n/other model name]: " "$RECOMMENDED"
        read -r model_choice < /dev/tty || model_choice=""
        model_choice="${model_choice:-y}"
        case "${model_choice,,}" in
            y|yes) MODEL="$RECOMMENDED" ;;
            n|no)  MODEL="" ;;
            *)     MODEL="$model_choice" ;;
        esac
    else
        MODEL="$RECOMMENDED"
    fi
fi

# Pull model if not already available
if [[ -z "$MODEL" ]]; then
    ok "Skipping model pull"
elif ollama list 2>/dev/null | grep -q "$MODEL"; then
    ok "Model $MODEL already available"
else
    info "Pulling $MODEL (this may take a few minutes)..."
    if ollama pull "$MODEL"; then
        ok "Model $MODEL pulled"
    else
        warn "Model pull failed. Retry later with: ollama pull $MODEL"
    fi
fi

fi  # end client-mode skip for Phase 8

# ── Phase 9: Server setup (server or both mode only) ───────────────────────

if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    echo ""
    echo "[9/$total_phases] Configuring server..."

    # Firewall note
    info "macOS will prompt to allow incoming connections when the server first starts."
    info "Click 'Allow' when prompted."

    # launchd plist for auto-start
    PLIST_DIR="$HOME/Library/LaunchAgents"
    PLIST_PATH="$PLIST_DIR/com.tokenpal.server.plist"
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tokenpal.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/tokenpal-server</string>
        <string>--host</string>
        <string>0.0.0.0</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${TOKENPAL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/tokenpal-server.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/tokenpal-server.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_KEEP_ALIVE</key>
        <string>1m</string>
    </dict>
</dict>
</plist>
PLIST
    ok "Launchd plist created: $PLIST_PATH"
    info "Start server: launchctl load $PLIST_PATH"
    info "Stop server:  launchctl unload $PLIST_PATH"
    info "Logs: ~/Library/Logs/tokenpal-server.log"

    # HuggingFace token (optional, for fine-tuning gated models)
    echo ""
    info "HuggingFace token (optional, for fine-tuning gated models like Gemma)..."
    DATA_DIR="$HOME/.tokenpal"
    mkdir -p "$DATA_DIR"

    ENV_FILE="$DATA_DIR/server.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        touch "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    fi

    _write_hf_token() {
        local token="$1"
        if grep -q "^HF_TOKEN=" "$ENV_FILE" 2>/dev/null; then
            sed -i.bak "s|^HF_TOKEN=.*|HF_TOKEN=$token|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
        else
            echo "HF_TOKEN=$token" >> "$ENV_FILE"
        fi
        chmod 600 "$ENV_FILE"
    }

    if [[ -n "${HF_TOKEN:-}" ]]; then
        ok "HF_TOKEN already set in environment."
        _write_hf_token "$HF_TOKEN"
    elif [[ -t 0 ]]; then
        printf "  Paste your HF token (or press Enter to skip): "
        read -r hf_token < /dev/tty || hf_token=""
        if [[ -n "$hf_token" ]]; then
            _write_hf_token "$hf_token"
            ok "Saved to $ENV_FILE"
        else
            info "Skipped. Only needed for fine-tuning gated models."
        fi
    else
        info "Non-interactive. Set HF_TOKEN in environment or $DATA_DIR/server.env later."
    fi

    # Adjust phase offset for remaining phases
    CONFIG_PHASE=10
    VALIDATE_PHASE=11
    SUMMARY_PHASE=12
else
    CONFIG_PHASE=9
    VALIDATE_PHASE=10
    SUMMARY_PHASE=11
fi

# ── Config setup ────────────────────────────────────────────────────────────

echo ""
echo "[$CONFIG_PHASE/$total_phases] Setting up config..."
CONFIG_PATH="$TOKENPAL_DIR/config.toml"
DEFAULT_CONFIG_PATH="$TOKENPAL_DIR/config.default.toml"

if [[ -f "$CONFIG_PATH" ]]; then
    ok "config.toml already exists"
elif [[ -f "$DEFAULT_CONFIG_PATH" ]]; then
    cp "$DEFAULT_CONFIG_PATH" "$CONFIG_PATH"
    ok "Created config.toml from defaults"
else
    warn "config.default.toml not found. Skipping config setup."
fi

# Client mode: ask which remote inference server to connect to
if [[ "$MODE" == "client" && -f "$CONFIG_PATH" ]]; then
    SERVER_TARGET="${TOKENPAL_SERVER:-}"
    if [[ -z "$SERVER_TARGET" && -t 0 ]]; then
        echo ""
        info "Client mode: which inference server should the buddy connect to?"
        info "  Enter hostname (becomes http://host:8585/v1) or full URL,"
        info "  or leave blank to configure later via /server switch."
        printf "  Server: "
        read -r SERVER_TARGET < /dev/tty || SERVER_TARGET=""
    fi

    SERVER_TARGET="$(echo "$SERVER_TARGET" | tr -d '[:space:]')"
    if [[ -n "$SERVER_TARGET" ]]; then
        if [[ "$SERVER_TARGET" == http://* || "$SERVER_TARGET" == https://* ]]; then
            SERVER_URL="${SERVER_TARGET%/}"
        else
            SERVER_URL="http://$SERVER_TARGET:8585/v1"
        fi
        if [[ "$SERVER_URL" != */v1 ]]; then
            SERVER_URL="$SERVER_URL/v1"
        fi
        MODEL_SUGGESTED=$("$VENV_DIR/bin/python" - "$CONFIG_PATH" "$SERVER_URL" <<'PY' 2>/dev/null
import json, pathlib, sys, tomllib, urllib.request
import tomli_w
path, url = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
data = tomllib.loads(p.read_text())
data.setdefault("llm", {})["api_url"] = url

base = url.rstrip("/")
if base.endswith("/v1"):
    base = base[:-3]

models = []
for endpoint, key in (("/api/v1/models/list", None), ("/api/tags", "models")):
    try:
        with urllib.request.urlopen(f"{base}{endpoint}", timeout=5) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        models = parsed if key is None else parsed.get(key, [])
        if models:
            break
    except Exception:
        continue

if models:
    models.sort(key=lambda m: m.get("size") or 0, reverse=True)
    picked = models[0]
    data["llm"]["model_name"] = picked["name"]
    size_gb = (picked.get("size") or 0) / 1e9
    print(f"{picked['name']}|{size_gb:.1f}")

p.write_text(tomli_w.dumps(data))
PY
)
        if [[ $? -eq 0 ]]; then
            ok "Client points at $SERVER_URL"
            if [[ -n "$MODEL_SUGGESTED" ]]; then
                M_NAME="${MODEL_SUGGESTED%|*}"
                M_SIZE="${MODEL_SUGGESTED#*|}"
                ok "Detected server model: $M_NAME (${M_SIZE} GB) saved to [llm] model_name"
            else
                warn "Could not list models on $SERVER_URL. Keeping default model_name."
                warn "Run /model list after launch to see what the server has."
            fi
        else
            warn "Could not write api_url. Edit $CONFIG_PATH manually ([llm] api_url)."
        fi
    else
        warn "No server set. The buddy will fail on launch."
        warn "Fix by editing [llm] api_url in $CONFIG_PATH or running /server switch."
    fi
fi

# ── Validate ────────────────────────────────────────────────────────────────

echo ""
echo "[$VALIDATE_PHASE/$total_phases] Validating installation..."
if "$VENV_DIR/bin/tokenpal" --validate 2>/dev/null; then
    ok "Validation passed"
else
    # --validate may not exist yet; fall back to --check
    if "$VENV_DIR/bin/tokenpal" --check 2>/dev/null; then
        ok "Health check passed (--validate not yet available, used --check)"
    else
        warn "Validation/check had warnings (see above). TokenPal may still work."
    fi
fi

# ── macOS permissions reminder ──────────────────────────────────────────────

echo ""
info "macOS permissions reminder:"
info "  TokenPal needs Accessibility permission for idle detection."
info "  Go to: System Settings > Privacy & Security > Accessibility"
info "  Grant permission to your terminal app (Terminal, iTerm2, Ghostty, etc.)"

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "[$SUMMARY_PHASE/$total_phases] Done!"
echo ""
echo "=== TokenPal Installation Complete ==="
echo ""
echo "  Install directory: $TOKENPAL_DIR"
echo "  Virtual environment: $VENV_DIR"
echo "  Mode: $MODE"
echo "  Model: $MODEL"
echo ""
echo "  Next steps:"
echo "    1. Activate the venv:   source $VENV_DIR/bin/activate"
echo "    2. Start Ollama:        ollama serve   (if not already running)"
if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
echo "    3. Run TokenPal:        tokenpal"
echo "    4. Health check:        tokenpal --check"
fi
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
echo "    3. Start server:        launchctl load ~/Library/LaunchAgents/com.tokenpal.server.plist"
echo "       Or manually:         tokenpal-server --host 0.0.0.0"
echo "       Test from client:    curl http://$(hostname):8585/api/v1/server/info"
fi
echo ""
echo "  Config:  $CONFIG_PATH"
echo "  Logs:    ~/.tokenpal/logs/tokenpal.log"
echo ""
echo "  On first run, TokenPal will walk you through a quick setup wizard."
echo ""

# ── Offer to launch ────────────────────────────────────────────────────────
if [[ -t 0 ]]; then
    launch_choice=""
    case "$MODE" in
        client)
            printf "Launch TokenPal now? [y/N]: "
            read -r ans < /dev/tty || ans=""
            [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] && launch_choice="client"
            ;;
        server)
            printf "Launch tokenpal-server now? [y/N]: "
            read -r ans < /dev/tty || ans=""
            [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] && launch_choice="server"
            ;;
        both)
            printf "Launch now? [c]lient / [s]erver / [n]one: "
            read -r ans < /dev/tty || ans=""
            case "${ans,,}" in
                c|client) launch_choice="client" ;;
                s|server) launch_choice="server" ;;
            esac
            ;;
    esac

    source "$VENV_DIR/bin/activate"
    if [[ "$launch_choice" == "client" ]]; then
        info "Launching tokenpal..."
        exec "$VENV_DIR/bin/tokenpal"
    elif [[ "$launch_choice" == "server" ]]; then
        info "Launching tokenpal-server (Ctrl-C to stop)..."
        exec "$VENV_DIR/bin/tokenpal-server" --host 0.0.0.0
    fi
fi
