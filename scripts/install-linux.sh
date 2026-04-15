#!/usr/bin/env bash
set -euo pipefail

# TokenPal Linux Installer — standalone, idempotent
# Handles everything from a bare machine to a working TokenPal installation.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/install-linux.sh | bash
#   bash scripts/install-linux.sh --mode server
#
# Environment variables:
#   TOKENPAL_DIR    — install directory (default: $HOME/tokenpal)
#   TOKENPAL_MODEL  — Ollama model to pull (default: gemma4)
#   TOKENPAL_PORT   — server port (default: 8585)

REPO_URL="https://github.com/smabe/TokenPal.git"
INSTALL_DIR="${TOKENPAL_DIR:-$HOME/tokenpal}"
MODEL="${TOKENPAL_MODEL:-gemma4}"
PORT="${TOKENPAL_PORT:-8585}"
MODE=""

# ── Argument parsing ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="${2,,}"  # lowercase
            shift 2
            if [[ "$MODE" != "client" && "$MODE" != "server" && "$MODE" != "both" ]]; then
                echo "ERROR: --mode must be client, server, or both"
                exit 1
            fi
            ;;
        -h|--help)
            echo "Usage: install-linux.sh [--mode client|server|both]"
            echo ""
            echo "Environment variables:"
            echo "  TOKENPAL_DIR    install directory (default: ~/tokenpal)"
            echo "  TOKENPAL_MODEL  Ollama model (default: gemma4)"
            echo "  TOKENPAL_PORT   server port (default: 8585)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${BOLD}$1${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "  ${YELLOW}!${RESET} $1"; }
fail()  { echo -e "  ${RED}✗${RESET} $1"; }

# ── Pre-flight: Linux only ──────────────────────────────────────────────────

if [[ "$(uname)" != "Linux" ]]; then
    fail "This installer is for Linux only. See scripts/install-macos.sh or scripts/install-windows.ps1."
    exit 1
fi

echo ""
info "=== TokenPal Linux Installer ==="
echo ""

# ── Phase 1: Package manager detection + system packages ───────────────────

info "[1/9] Installing system packages..."

install_packages() {
    if command -v apt-get &>/dev/null; then
        PKG_MGR="apt-get"
        sudo apt-get update -qq
        # Try python3.12 first, fall back to python3 (Ubuntu 24.04+ ships 3.12+)
        if apt-cache show python3.12 &>/dev/null 2>&1; then
            sudo apt-get install -y python3.12 python3.12-venv python3.12-dev git build-essential
        else
            sudo apt-get install -y python3 python3-venv python3-dev git build-essential
        fi
    elif command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
        sudo dnf install -y python3.12 python3.12-devel git gcc gcc-c++ make
    elif command -v pacman &>/dev/null; then
        PKG_MGR="pacman"
        sudo pacman -Syu --noconfirm --needed python python-pip git base-devel
    elif command -v zypper &>/dev/null; then
        PKG_MGR="zypper"
        sudo zypper install -y python312 python312-devel python312-venv git gcc gcc-c++ make
    else
        fail "No supported package manager found (apt-get, dnf, pacman, zypper)."
        echo "  Install Python 3.12+, git, and a C compiler manually, then re-run."
        exit 1
    fi
    ok "System packages installed via $PKG_MGR"
}

install_packages

# ── Phase 2: Python 3.12+ verification ─────────────────────────────────────

info "[2/9] Verifying Python 3.12+..."

PYTHON=""
for candidate in python3.12 python3.13 python3.14 python3; do
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
    fail "Python 3.12+ not found after package install."
    echo "  Your distro may not ship Python 3.12+. Options:"
    echo "    - Use pyenv: curl https://pyenv.run | bash && pyenv install 3.12"
    echo "    - Use deadsnakes PPA (Ubuntu): sudo add-apt-repository ppa:deadsnakes/ppa"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python $PY_VER ($PYTHON)"

# Ensure venv module is available
if ! "$PYTHON" -m venv --help &>/dev/null; then
    fail "Python venv module not available. Install python3-venv for your distro."
    exit 1
fi

# ── Phase 3: Feature selection ──────────────────────────────────────────────

info "[3/9] Feature selection..."

if [[ -z "$MODE" ]]; then
    if [[ -t 0 ]]; then
        echo ""
        echo "  How would you like to install TokenPal?"
        echo "    [C] Client only — run the buddy on this machine"
        echo "    [S] Server only — serve LLM inference for other machines"
        echo "    [B] Both — full installation"
        echo ""
        read -rp "  Choice [C/s/b]: " choice
        choice="${choice:-c}"
        case "${choice,,}" in
            c|client)  MODE="client" ;;
            s|server)  MODE="server" ;;
            b|both)    MODE="both"   ;;
            *)         MODE="client" ; warn "Invalid choice, defaulting to client" ;;
        esac
    else
        MODE="client"
        warn "Non-interactive shell detected, defaulting to client mode"
    fi
fi

ok "Mode: $MODE"

# ── Phase 4: Clone or update repo ──────────────────────────────────────────

info "[4/9] Setting up repository..."

# Check if we're already inside a tokenpal repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
IN_REPO=false

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
    if grep -q 'name = "tokenpal"' "$SCRIPT_DIR/../pyproject.toml" 2>/dev/null; then
        INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        IN_REPO=true
        ok "Already in TokenPal repo: $INSTALL_DIR"
    fi
fi

if [[ "$IN_REPO" == false ]]; then
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        ok "Repo exists, pulling latest..."
        git -C "$INSTALL_DIR" pull --ff-only || warn "Pull failed — continuing with existing code"
    else
        echo "  Cloning TokenPal to $INSTALL_DIR..."
        git clone "$REPO_URL" "$INSTALL_DIR"
        ok "Cloned to $INSTALL_DIR"
    fi
fi

# ── Phase 5: Venv + pip install ─────────────────────────────────────────────

info "[5/9] Setting up Python environment..."

VENV_DIR="$INSTALL_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created virtual environment"
else
    ok "Virtual environment exists"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q

# Determine extras based on mode
case "$MODE" in
    client) EXTRAS="dev"        ;;
    server) EXTRAS="server,dev" ;;
    both)   EXTRAS="server,dev" ;;
esac

echo "  Installing tokenpal[$EXTRAS]..."
pip install -e "$INSTALL_DIR[$EXTRAS]" -q
ok "Installed tokenpal[$EXTRAS]"

# ── Phase 6: Ollama ────────────────────────────────────────────────────────

info "[6/9] Setting up Ollama..."

if [[ "$MODE" == "client" ]]; then
    ok "Client mode — skipping local Ollama install (inference happens on remote server)"
    info "If you want a local fallback, install later with: curl -fsSL https://ollama.com/install.sh | sh"
else

if ! command -v ollama &>/dev/null; then
    echo "  Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed"
else
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'present')"
fi

# Start Ollama if not running, with health check loop
if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    echo "  Starting Ollama..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!

    elapsed=0
    while ! curl -sf http://localhost:11434/ >/dev/null 2>&1; do
        if (( elapsed >= 30 )); then
            fail "Ollama failed to start within 30 seconds."
            echo "  Try manually: ollama serve"
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if curl -sf http://localhost:11434/ >/dev/null 2>&1; then
        ok "Ollama is running (pid $OLLAMA_PID)"
    fi
else
    ok "Ollama is already running"
fi

# Recommend model based on VRAM (server/both mode)
if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    vram_gb=0
    # Try NVIDIA
    if command -v nvidia-smi &>/dev/null; then
        vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || echo 0)
        vram_gb=$(( vram_mb / 1024 ))
        info "Detected NVIDIA GPU with ~${vram_gb}GB VRAM"
    fi
    # Try AMD (sysfs)
    if (( vram_gb == 0 )); then
        for mem_file in /sys/class/drm/card*/device/mem_info_vram_total; do
            if [[ -r "$mem_file" ]]; then
                vram_bytes=$(cat "$mem_file" 2>/dev/null || echo 0)
                candidate_gb=$(( vram_bytes / 1073741824 ))
                if (( candidate_gb > vram_gb )); then
                    vram_gb=$candidate_gb
                fi
            fi
        done
        if (( vram_gb > 0 )); then
            info "Detected AMD GPU with ~${vram_gb}GB VRAM"
        fi
    fi
    # Fall back to system RAM
    if (( vram_gb == 0 )); then
        total_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
        vram_gb=$(( total_kb / 1048576 ))
        info "No discrete GPU VRAM detected — using system RAM (${vram_gb}GB) for model recommendation"
    fi

    # Tiers. For reasoning models (deepseek-r1, qwq), override via TOKENPAL_MODEL.
    # Note: TokenPal strips think tags and sends reasoning_effort=none, so reasoning
    # models are best for /ask and tool flows, not observation commentary.
    if (( vram_gb >= 48 )); then
        RECOMMENDED="llama3.3:70b"
        echo "  Recommending llama3.3:70b (70B, best quality for ${vram_gb}GB)"
    elif (( vram_gb >= 32 )); then
        RECOMMENDED="qwen2.5:32b"
        echo "  Recommending qwen2.5:32b (32B) for ${vram_gb}GB. gemma4:26b also fits with headroom."
    elif (( vram_gb >= 16 )); then
        RECOMMENDED="gemma4:26b"
        echo "  Recommending gemma4:26b (26B, best quality for ${vram_gb}GB)"
    elif (( vram_gb >= 6 )); then
        RECOMMENDED="gemma4"
        echo "  Recommending gemma4 (9B, solid default for ${vram_gb}GB)"
    else
        RECOMMENDED="gemma2:2b"
        echo "  Recommending gemma2:2b (2B, fits in ${vram_gb}GB)"
    fi

    # Let user confirm or override
    if [[ -t 0 ]] && [[ "$MODEL" == "${TOKENPAL_MODEL:-gemma4}" ]]; then
        printf "  Pull %s? [Y/n/other model name]: " "$RECOMMENDED"
        read -r model_choice || model_choice=""
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

# Pull model
if [[ -z "$MODEL" ]]; then
    ok "Skipping model pull"
elif curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    if ollama list 2>/dev/null | grep -q "$MODEL"; then
        ok "Model $MODEL already available"
    else
        echo "  Pulling $MODEL (this may take a few minutes)..."
        ollama pull "$MODEL" && ok "Model $MODEL pulled" || warn "Model pull failed — retry later: ollama pull $MODEL"
    fi
else
    warn "Ollama not reachable — pull $MODEL manually: ollama pull $MODEL"
fi

fi  # end client-mode skip for Phase 6

# ── Phase 7: Server-specific setup ─────────────────────────────────────────

if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    info "[7/9] Server configuration..."

    # --- Firewall ---
    echo "  Configuring firewall for port $PORT..."
    if command -v ufw &>/dev/null; then
        sudo ufw allow "$PORT/tcp" comment "TokenPal Server" 2>/dev/null && \
            ok "ufw: allowed port $PORT/tcp" || \
            warn "Could not add ufw rule. Run: sudo ufw allow $PORT/tcp"
    elif command -v firewall-cmd &>/dev/null; then
        sudo firewall-cmd --permanent --add-port="$PORT/tcp" 2>/dev/null && \
            sudo firewall-cmd --reload 2>/dev/null && \
            ok "firewalld: allowed port $PORT/tcp" || \
            warn "Could not add firewalld rule. Run: sudo firewall-cmd --permanent --add-port=$PORT/tcp && sudo firewall-cmd --reload"
    elif command -v iptables &>/dev/null; then
        warn "No ufw/firewalld detected. If using iptables/nftables, open port $PORT manually:"
        echo "    iptables:  sudo iptables -A INPUT -p tcp --dport $PORT -j ACCEPT"
        echo "    nftables:  nft add rule inet filter input tcp dport $PORT accept"
    else
        warn "No firewall manager detected. Manually open port $PORT/tcp if needed."
    fi

    # --- Systemd user unit ---
    if command -v systemctl &>/dev/null; then
        UNIT_DIR="$HOME/.config/systemd/user"
        mkdir -p "$UNIT_DIR"
        DATA_DIR="$HOME/.tokenpal"
        mkdir -p "$DATA_DIR"

        ENV_FILE="$DATA_DIR/server.env"
        # Create env file if it doesn't exist (systemd EnvironmentFile needs it)
        if [[ ! -f "$ENV_FILE" ]]; then
            touch "$ENV_FILE"
            chmod 600 "$ENV_FILE"
        fi

        cat > "$UNIT_DIR/tokenpal-server.service" <<UNIT
[Unit]
Description=TokenPal Server (LLM inference + training)
After=network-online.target

[Service]
Type=exec
ExecStart=$VENV_DIR/bin/tokenpal-server --host 0.0.0.0
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

        systemctl --user daemon-reload
        systemctl --user enable tokenpal-server
        loginctl enable-linger "$USER" 2>/dev/null || true
        ok "Systemd user unit installed and enabled"
        echo "    Start:  systemctl --user start tokenpal-server"
        echo "    Logs:   journalctl --user -u tokenpal-server -f"
    else
        warn "systemd not found. Start the server manually:"
        echo "    cd $INSTALL_DIR && source .venv/bin/activate && tokenpal-server --host 0.0.0.0"
    fi

    # --- HuggingFace token ---
    echo ""
    echo "  HuggingFace token (needed for fine-tuning gated models like Gemma):"
    DATA_DIR="$HOME/.tokenpal"
    mkdir -p "$DATA_DIR"
    ENV_FILE="$DATA_DIR/server.env"

    if [[ -n "${HF_TOKEN:-}" ]]; then
        # Merge into env file, replacing existing line
        if grep -q "^HF_TOKEN=" "$ENV_FILE" 2>/dev/null; then
            sed -i "s|^HF_TOKEN=.*|HF_TOKEN=$HF_TOKEN|" "$ENV_FILE"
        else
            echo "HF_TOKEN=$HF_TOKEN" >> "$ENV_FILE"
        fi
        chmod 600 "$ENV_FILE"
        ok "HF_TOKEN saved from environment to $ENV_FILE"
    elif [[ -t 0 ]]; then
        echo -n "  Paste your HF token (or press Enter to skip): "
        read -r hf_token
        if [[ -n "$hf_token" ]]; then
            if grep -q "^HF_TOKEN=" "$ENV_FILE" 2>/dev/null; then
                sed -i "s|^HF_TOKEN=.*|HF_TOKEN=$hf_token|" "$ENV_FILE"
            else
                echo "HF_TOKEN=$hf_token" >> "$ENV_FILE"
            fi
            chmod 600 "$ENV_FILE"
            ok "Saved to $ENV_FILE"
        else
            ok "Skipped — only needed for fine-tuning gated models"
        fi
    else
        warn "Non-interactive — set HF_TOKEN in $ENV_FILE manually if needed for fine-tuning"
    fi
else
    info "[7/9] Server configuration... skipped (client mode)"
fi

# ── Phase 8: Config + NVIDIA detection ──────────────────────────────────────

info "[8/9] Configuration and hardware detection..."

# Copy config.default.toml → config.toml if missing
CONFIG_DEFAULT="$INSTALL_DIR/config.default.toml"
CONFIG_FILE="$INSTALL_DIR/config.toml"

if [[ -f "$CONFIG_FILE" ]]; then
    ok "config.toml already exists"
elif [[ -f "$CONFIG_DEFAULT" ]]; then
    cp "$CONFIG_DEFAULT" "$CONFIG_FILE"
    ok "Created config.toml from defaults"
else
    warn "config.default.toml not found — config.toml must be created manually"
fi

# Client mode: ask which remote inference server to connect to
if [[ "$MODE" == "client" && -f "$CONFIG_FILE" ]]; then
    SERVER_TARGET="${TOKENPAL_SERVER:-}"
    if [[ -z "$SERVER_TARGET" && -t 0 ]]; then
        echo ""
        info "Client mode: which inference server should the buddy connect to?"
        info "  Enter hostname (becomes http://host:8585/v1) or full URL,"
        info "  or leave blank to configure later via /server switch."
        printf "  Server: "
        read -r SERVER_TARGET || SERVER_TARGET=""
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
        MODEL_SUGGESTED=$("$VENV_DIR/bin/python" - "$CONFIG_FILE" "$SERVER_URL" <<'PY' 2>/dev/null
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
            warn "Could not write api_url. Edit $CONFIG_FILE manually ([llm] api_url)."
        fi
    else
        warn "No server set. The buddy will fail on launch."
        warn "Fix by editing [llm] api_url in $CONFIG_FILE or running /server switch."
    fi
fi

# NVIDIA GPU detection
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "detected")
    ok "NVIDIA GPU found: $GPU_NAME"
    echo "    For GPU monitoring in TokenPal, install the nvidia extra:"
    echo "    source $VENV_DIR/bin/activate && pip install -e \"$INSTALL_DIR[nvidia]\""
else
    echo "  No NVIDIA GPU detected (nvidia-smi not found)"
fi

# ── Phase 9: Validation ────────────────────────────────────────────────────

info "[9/9] Validating installation..."

# Basic import check
if "$VENV_DIR/bin/python" -c "from tokenpal.app import main; print('OK')" 2>/dev/null | grep -q "OK"; then
    ok "TokenPal imports successfully"
else
    warn "Import check failed — run 'tokenpal --check' for details"
fi

# Run --check if available
if "$VENV_DIR/bin/tokenpal" --check 2>/dev/null; then
    ok "tokenpal --check passed"
else
    warn "tokenpal --check reported issues (non-fatal)"
fi

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
info "════════════════════════════════════════════════════"
info "  TokenPal installation complete!"
info "════════════════════════════════════════════════════"
echo ""

echo "  Install directory: $INSTALL_DIR"
echo "  Virtual env:       $VENV_DIR"
echo "  Config:            $CONFIG_FILE"
echo "  Mode:              $MODE"
echo ""

echo -e "${BOLD}Next steps:${RESET}"
echo ""

if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    echo "  Start TokenPal:"
    echo "    cd $INSTALL_DIR"
    echo "    source .venv/bin/activate"
    echo "    tokenpal"
    echo ""
fi

if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
    echo "  Start the server:"
    echo "    systemctl --user start tokenpal-server"
    echo ""
    echo "  Or manually:"
    echo "    cd $INSTALL_DIR && source .venv/bin/activate"
    echo "    tokenpal-server --host 0.0.0.0"
    echo ""
    echo "  Test from another machine:"
    echo "    curl http://$(hostname):$PORT/api/v1/server/info"
    echo ""
fi

# Client-specific notes for Linux
if [[ "$MODE" == "client" || "$MODE" == "both" ]]; then
    echo -e "${YELLOW}Linux notes:${RESET}"
    echo "  - app_awareness sense is not available on Linux (macOS only)"
    echo "  - music sense is not available on Linux (macOS only)"
    echo "  - idle detection (pynput) requires X11 or Wayland with XWayland"
    echo "  - Available senses: hardware, time, weather, git, productivity, idle"
    echo ""
fi

# Tailscale hostname if available
if command -v tailscale &>/dev/null; then
    ts_hostname=$(tailscale status --json 2>/dev/null | "$VENV_DIR/bin/python" -c "import sys,json; d=json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
    if [[ -n "$ts_hostname" ]]; then
        echo -e "${GREEN}Tailscale:${RESET} $ts_hostname"
        if [[ "$MODE" == "server" || "$MODE" == "both" ]]; then
            echo "  Client config.toml:"
            echo "    [llm]"
            echo "    api_url = \"http://$ts_hostname:$PORT/v1\""
        fi
        echo ""
    fi
fi

echo "  Logs:    ~/.tokenpal/logs/tokenpal.log"
echo "  Health:  tokenpal --check"
echo ""

# ── Offer to launch ────────────────────────────────────────────────────────
if [[ -t 0 ]]; then
    launch_choice=""
    case "$MODE" in
        client)
            printf "Launch TokenPal now? [y/N]: "
            read -r ans || ans=""
            [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] && launch_choice="client"
            ;;
        server)
            printf "Launch tokenpal-server now? [y/N]: "
            read -r ans || ans=""
            [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] && launch_choice="server"
            ;;
        both)
            printf "Launch now? [c]lient / [s]erver / [n]one: "
            read -r ans || ans=""
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
