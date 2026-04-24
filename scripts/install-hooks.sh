#!/usr/bin/env bash
# Install git hook shims that delegate to repo-tracked hooks under
# .claude/hooks/. Idempotent and safe to re-run.
#
# Currently installs:
#   .git/hooks/pre-commit -> .claude/hooks/pre-commit (docs-verification)
#
# Coexists with other hooks already in .git/hooks/ (e.g. graphify's
# post-commit/post-checkout) — we only write pre-commit here.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "install-hooks: not a git repo" >&2
    exit 1
}

GIT_HOOK="$REPO_ROOT/.git/hooks/pre-commit"
TRACKED_HOOK="$REPO_ROOT/.claude/hooks/pre-commit"

if [[ ! -x "$TRACKED_HOOK" ]]; then
    echo "install-hooks: $TRACKED_HOOK missing or not executable" >&2
    exit 1
fi

# If a non-shim pre-commit is already installed, don't clobber.
if [[ -e "$GIT_HOOK" ]] && ! grep -q "claude/hooks/pre-commit" "$GIT_HOOK" 2>/dev/null; then
    echo "install-hooks: $GIT_HOOK exists and is not our shim; leaving it alone." >&2
    echo "install-hooks: inspect it and merge manually if needed." >&2
    exit 1
fi

cat > "$GIT_HOOK" <<'SHIM'
#!/usr/bin/env bash
# Shim: delegate to repo-tracked hook at .claude/hooks/pre-commit.
# Installed by scripts/install-hooks.sh — do not edit here.
set -e
HOOK="$(git rev-parse --show-toplevel)/.claude/hooks/pre-commit"
if [[ -x "$HOOK" ]]; then
    exec "$HOOK" "$@"
fi
exit 0
SHIM
chmod +x "$GIT_HOOK"

echo "install-hooks: pre-commit shim installed at $GIT_HOOK"

# --- Post-install sanity: WebFetch must be pre-approved ---------------------
# The nested `claude -p` inside the hook uses WebFetch to fetch official docs.
# In a non-interactive git-commit context there's no TTY to accept a prompt,
# so WebFetch must be on permissions.allow in one of the settings files.
LOCAL_SETTINGS="$REPO_ROOT/.claude/settings.local.json"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"
has_webfetch=0
for f in "$LOCAL_SETTINGS" "$GLOBAL_SETTINGS"; do
    [[ -f "$f" ]] || continue
    if python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as fp:
        data = json.load(fp)
except Exception:
    sys.exit(1)
allow = data.get("permissions", {}).get("allow", [])
sys.exit(0 if "WebFetch" in allow else 1)
' "$f" 2>/dev/null; then
        has_webfetch=1
        break
    fi
done

if [[ "$has_webfetch" == "0" ]]; then
    cat >&2 <<MSG
install-hooks: WARNING - WebFetch is not pre-approved in any settings file.
install-hooks: The docs-verification hook will fail at the nested claude call
install-hooks: until you add "WebFetch" to permissions.allow in:
install-hooks:   $LOCAL_SETTINGS   (repo-scoped, recommended)
install-hooks:   or $GLOBAL_SETTINGS   (global)
install-hooks:
install-hooks: Minimal repo-scoped snippet:
install-hooks:   { "permissions": { "allow": ["WebFetch"] } }
MSG
fi
