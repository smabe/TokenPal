#!/usr/bin/env bash
set -euo pipefail

# TokenPal Server Installer — Linux / macOS
# Sets up: Ollama, model, Python venv, tokenpal[server], firewall, systemd

INSTALL_DIR="${TOKENPAL_SERVER_DIR:-$HOME/.tokenpal}"
VENV_DIR="$INSTALL_DIR/server-venv"
PORT="${TOKENPAL_PORT:-8585}"
MODEL="${TOKENPAL_MODEL:-gemma4}"
PYTHON="${PYTHON:-python3}"

echo "=== TokenPal Server Setup ==="
echo "Install dir: $INSTALL_DIR"
echo ""

# --- Phase 1: Python check ---
echo "[1/7] Checking Python..."
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found. Install Python 3.12+."
    exit 1
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
if (( PY_MINOR < 12 )); then
    echo "ERROR: Python 3.12+ required, found $PY_VER"
    exit 1
fi
echo "  Python $PY_VER OK"

# --- Phase 2: Ollama install ---
echo "[2/7] Checking Ollama..."
if ! command -v ollama &>/dev/null; then
    echo "  Ollama not found. Installing..."
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install ollama
        else
            echo "  Install Ollama from https://ollama.com/download"
            echo "  Or: brew install ollama"
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
"$PYTHON" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install 'tokenpal[server]' -q
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
echo "[6/7] HuggingFace token (for gated models like Gemma)..."
if [[ -z "${HF_TOKEN:-}" ]]; then
    echo -n "  Paste your HF token (or press Enter to skip): "
    read -r hf_token
    if [[ -n "$hf_token" ]]; then
        echo "HF_TOKEN=$hf_token" > "$INSTALL_DIR/server.env"
        chmod 600 "$INSTALL_DIR/server.env"
        echo "  Saved to $INSTALL_DIR/server.env"
    else
        echo "  Skipped. Set HF_TOKEN later for gated models."
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
WorkingDirectory=$INSTALL_DIR
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
    echo "  No systemd. Start manually with:"
    echo "    source $VENV_DIR/bin/activate"
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
