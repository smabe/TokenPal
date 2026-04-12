#!/usr/bin/env bash
set -euo pipefail

# TokenPal Server Installer — Linux / macOS
# Sets up: Python, Ollama, model, venv, tokenpal[server], firewall, systemd
# Run from inside the cloned TokenPal repo.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${TOKENPAL_SERVER_DIR:-$HOME/.tokenpal}"
PORT="${TOKENPAL_PORT:-8585}"
MODEL="${TOKENPAL_MODEL:-gemma4}"

echo "=== TokenPal Server Setup ==="
echo "Repo: $REPO_DIR"
echo ""

# --- Phase 1: Python ---
echo "[1/7] Checking Python..."
PYTHON=""
for candidate in python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" &>/dev/null; then
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
        if (( minor >= 12 )); then
            PYTHON="$candidate"
            break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "  Python 3.12+ not found. Installing..."
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install python@3.12
            PYTHON="python3.12"
        else
            echo "ERROR: Install Homebrew first (https://brew.sh) or Python 3.12+ from python.org"
            exit 1
        fi
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv
        PYTHON="python3.12"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3.12
        PYTHON="python3.12"
    else
        echo "ERROR: Could not install Python automatically. Install Python 3.12+ and re-run."
        exit 1
    fi
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python $PY_VER OK"

# --- Phase 2: Ollama ---
echo "[2/7] Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    echo "  Ollama not found. Installing..."
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install ollama
        else
            echo "  Install Ollama from https://ollama.com/download"
            exit 1
        fi
    else
        curl -fsSL https://ollama.com/install.sh | sh
    fi
fi
echo "  Ollama OK: $(ollama --version 2>/dev/null || echo 'installed')"

# --- Phase 3: Start Ollama + pull model ---
echo "[3/7] Ensuring Ollama is running and pulling model..."
if ! curl -sf http://localhost:11434/ >/dev/null 2>&1; then
    echo "  Starting Ollama..."
    ollama serve &>/dev/null &
    sleep 3
fi
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
    echo "  Pulling $MODEL (this may take a few minutes)..."
    ollama pull "$MODEL"
else
    echo "  Model $MODEL already available"
fi

# --- Phase 4: Venv + tokenpal[server] ---
echo "[4/7] Setting up Python environment..."
mkdir -p "$INSTALL_DIR"
VENV_DIR="$REPO_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -e "$REPO_DIR[server]" -q
echo "  tokenpal-server installed"

# --- Phase 5: Firewall ---
echo "[5/7] Configuring firewall..."
if [[ "$(uname)" == "Darwin" ]]; then
    echo "  macOS: firewall will prompt on first connection. No action needed."
elif command -v ufw &>/dev/null; then
    sudo ufw allow "$PORT/tcp" comment "TokenPal Server" 2>/dev/null || \
        echo "  WARNING: Could not add ufw rule. Run: sudo ufw allow $PORT/tcp"
elif command -v firewall-cmd &>/dev/null; then
    sudo firewall-cmd --permanent --add-port="$PORT/tcp" 2>/dev/null && \
        sudo firewall-cmd --reload 2>/dev/null || \
        echo "  WARNING: Could not add firewalld rule. Run: sudo firewall-cmd --permanent --add-port=$PORT/tcp"
else
    echo "  No firewall manager detected. Manually open port $PORT/tcp if needed."
fi

# --- Phase 6: HF Token ---
echo "[6/7] HuggingFace token (for fine-tuning gated models like Gemma)..."
if [[ -z "${HF_TOKEN:-}" ]]; then
    echo -n "  Paste your HF token (or press Enter to skip): "
    read -r hf_token
    if [[ -n "$hf_token" ]]; then
        echo "HF_TOKEN=$hf_token" > "$INSTALL_DIR/server.env"
        chmod 600 "$INSTALL_DIR/server.env"
        echo "  Saved to $INSTALL_DIR/server.env"
    else
        echo "  Skipped. Only needed for fine-tuning gated models."
    fi
else
    echo "  HF_TOKEN already set in environment."
    echo "HF_TOKEN=$HF_TOKEN" > "$INSTALL_DIR/server.env"
    chmod 600 "$INSTALL_DIR/server.env"
fi

# --- Phase 7: Systemd (Linux only) ---
echo "[7/7] Service setup..."
if [[ "$(uname)" == "Linux" ]] && command -v systemctl &>/dev/null; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/tokenpal-server.service" <<UNIT
[Unit]
Description=TokenPal Server (LLM inference + training)
After=network-online.target

[Service]
Type=exec
ExecStart=$VENV_DIR/bin/tokenpal-server --host 0.0.0.0
WorkingDirectory=$REPO_DIR
EnvironmentFile=$INSTALL_DIR/server.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

    systemctl --user daemon-reload
    systemctl --user enable tokenpal-server
    loginctl enable-linger "$USER" 2>/dev/null || true
    echo "  Systemd user unit installed."
    echo "  Start with: systemctl --user start tokenpal-server"
    echo "  Logs:       journalctl --user -u tokenpal-server -f"
else
    echo "  Start manually with:"
    echo "    cd $REPO_DIR && source .venv/bin/activate"
    echo "    tokenpal-server --host 0.0.0.0"
fi

echo ""
echo "=== Setup Complete ==="
echo "Test from another machine:"
echo "  curl http://$(hostname):$PORT/api/v1/server/info"
echo ""
echo "Client config.toml:"
echo "  [llm]"
echo "  api_url = \"http://$(hostname):$PORT/v1\""
