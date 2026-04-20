#!/usr/bin/env bash
# Quick launcher for TokenPal - activates venv, auto-syncs deps if
# pyproject.toml changed since last launch, then runs tokenpal.
#
# Force a full resync: TOKENPAL_FORCE_SYNC=1 ./run.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYPROJECT="$SCRIPT_DIR/pyproject.toml"
MARKER="$VENV/.tokenpal-deps-synced"

if [ ! -d "$VENV" ]; then
    echo "No .venv found. Run: python3 setup_tokenpal.py"
    exit 1
fi

source "$VENV/bin/activate"

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
    if pip install -e "$SCRIPT_DIR[$extras]" --quiet; then
        touch "$MARKER"
        echo "Dependencies synced." >&2
    else
        echo "pip install failed. Launching anyway - tokenpal may crash on missing imports." >&2
    fi
fi

exec tokenpal "$@"
