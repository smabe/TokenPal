#!/usr/bin/env bash
# TokenPal one-liner installer.
#
#   curl -fsSL https://raw.githubusercontent.com/smabe/TokenPal/main/install.sh | bash
#
# Detects the platform and hands off to the matching platform installer.
# Args after `bash -s --` are forwarded (e.g. `| bash -s -- --mode server`).

set -euo pipefail

REPO="${TOKENPAL_REPO:-smabe/TokenPal}"
BRANCH="${TOKENPAL_BRANCH:-main}"
BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}/scripts"

case "$(uname -s)" in
    Darwin)  SCRIPT="install-macos.sh" ;;
    Linux)   SCRIPT="install-linux.sh" ;;
    MINGW*|MSYS*|CYGWIN*)
        echo "ERROR: Detected Windows shell. Use PowerShell instead:"
        echo "  iwr -useb https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.ps1 | iex"
        exit 1
        ;;
    *)
        echo "ERROR: Unsupported platform: $(uname -s)"
        exit 1
        ;;
esac

TMP="$(mktemp -t tokenpal-install.XXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT

echo "==> Downloading ${SCRIPT} from ${REPO}@${BRANCH}..."
curl -fsSL "${BASE}/${SCRIPT}" -o "$TMP"
chmod +x "$TMP"

# Force interactive prompts to read from the user's terminal, even when
# the bootstrap itself was piped from curl (stdin is the download, not a tty).
if [[ -t 1 ]] && [[ -e /dev/tty ]]; then
    exec bash "$TMP" "$@" < /dev/tty
else
    exec bash "$TMP" "$@"
fi
