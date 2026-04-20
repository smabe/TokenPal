#!/usr/bin/env bash
set -euo pipefail

# TokenPal launcher (macOS/Linux) — auto-syncs deps when pyproject.toml changes.
#
# Runs `pip install -e .[<extras>]` when the venv marker is older than
# pyproject.toml, then execs `tokenpal` with the passed args. Fast path
# (no pip call) when nothing has changed since last launch.
#
# Usage:
#   bash scripts/run-tokenpal.sh [tokenpal-args...]
#
# Force a full resync:
#   TOKENPAL_FORCE_SYNC=1 bash scripts/run-tokenpal.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$REPO_DIR/.venv"
PYPROJECT="$REPO_DIR/pyproject.toml"
MARKER="$VENV_DIR/.tokenpal-deps-synced"

if [ ! -d "$VENV_DIR" ]; then
    echo "No venv at $VENV_DIR." >&2
    case "$(uname)" in
        Darwin) echo "Run: bash scripts/install-macos.sh" >&2 ;;
        Linux)  echo "Run: bash scripts/install-linux.sh" >&2 ;;
        *)      echo "Run the platform installer first." >&2 ;;
    esac
    exit 1
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

needs_sync=0
if [ "${TOKENPAL_FORCE_SYNC:-0}" = "1" ]; then
    needs_sync=1
elif [ ! -f "$MARKER" ]; then
    needs_sync=1
elif [ "$PYPROJECT" -nt "$MARKER" ]; then
    needs_sync=1
fi

if [ "$needs_sync" = "1" ]; then
    case "$(uname)" in
        Darwin) extras="macos,dev" ;;
        Linux)  extras="dev" ;;
        *)      extras="dev" ;;
    esac
    echo "Syncing tokenpal[$extras]..." >&2
    if pip install -e "$REPO_DIR[$extras]" --quiet; then
        touch "$MARKER"
        echo "Dependencies synced." >&2
    else
        echo "pip install failed. Launching anyway — tokenpal may crash on missing imports." >&2
    fi
fi

exec tokenpal "$@"
