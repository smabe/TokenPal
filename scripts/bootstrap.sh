#!/usr/bin/env bash
set -euo pipefail

# TokenPal Server — One-Line Bootstrap
# Paste this into a terminal on the GPU machine:
#   curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/scripts/bootstrap.sh | bash

# DEPRECATED: This script is superseded by install-linux.sh / install-macos.sh.
# It is kept for backward compatibility only.

echo "NOTE: This script is maintained for backward compatibility."
echo "For fresh installs, prefer: bash scripts/install-linux.sh (or install-macos.sh)"
echo ""

REPO_URL="https://github.com/smabe/TokenPal.git"
INSTALL_DIR="$HOME/tokenpal-server"
PORT="${TOKENPAL_PORT:-8585}"

echo ""
echo "  TokenPal Server Bootstrap"
echo "  ========================="
echo ""

# Check for git
if ! command -v git &>/dev/null; then
    echo "Installing git..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y git
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y git
    elif command -v brew &>/dev/null; then
        brew install git
    else
        echo "ERROR: git not found. Install git and re-run."
        exit 1
    fi
fi

# Clone or update
if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "Updating existing repo..."
    cd "$INSTALL_DIR" && git pull
else
    echo "Cloning TokenPal..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# Run the full installer
echo ""
cd "$INSTALL_DIR"
bash scripts/install-server.sh

# Print connection info
echo ""
echo "========================================"
echo "  Server is ready!"
echo "========================================"
echo ""

hostname=$(hostname)

# Try to get Tailscale hostname
ts_hostname=""
if command -v tailscale &>/dev/null; then
    ts_hostname=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
fi

echo "Tell your friends to add this to their config.toml:"
echo ""
echo "  [llm]"
if [[ -n "$ts_hostname" ]]; then
    echo "  api_url = \"http://$ts_hostname:$PORT/v1\"    # Tailscale"
    echo ""
    echo "  Or on local network:"
fi
echo "  api_url = \"http://$hostname:$PORT/v1\""
echo ""
echo "Start the server anytime:"
echo "  cd $INSTALL_DIR && source .venv/bin/activate && tokenpal-server --host 0.0.0.0"
echo ""
