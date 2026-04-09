#!/usr/bin/env bash
# Quick launcher for TokenPal — activates venv and runs.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "No .venv found. Run: python3 setup_tokenpal.py"
    exit 1
fi

source "$VENV/bin/activate"
exec tokenpal "$@"
