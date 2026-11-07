#!/usr/bin/env bash
# Install Stash into this machine's Hermes profile.
# Safe to re-run: it relinks, re-enables and re-materialises the desktop half.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS="$HERMES_HOME/plugins"
DESKTOP="$HERMES_HOME/desktop-plugins/stash"

say() { printf '  %s\n' "$1"; }

[ -d "$HERMES_HOME" ] || { echo "No Hermes home at $HERMES_HOME — install Hermes Agent first."; exit 1; }

echo "Installing Stash into $HERMES_HOME"

mkdir -p "$PLUGINS"
if [ -e "$PLUGINS/stash" ] && [ ! -L "$PLUGINS/stash" ]; then
  echo "  $PLUGINS/stash already exists and is not a symlink — moving it aside"
  mv "$PLUGINS/stash" "$PLUGINS/stash.bak.$(date +%s)"
fi
ln -sfn "$SRC" "$PLUGINS/stash"
say "linked $PLUGINS/stash -> $SRC"

# The desktop half has to sit in desktop-plugins/ for the app to see it. A local
# Electron build copies this itself; doing it here means a reload is enough.
mkdir -p "$DESKTOP"
cp "$SRC/desktop/plugin.js" "$DESKTOP/plugin.js"
python3 - "$SRC/desktop" "$DESKTOP/.hermes-package.json" <<'PY'
import json, os, sys
src, out = sys.argv[1], sys.argv[2]
payload = {
    "package": "stash",
    "source": src,
    "sourceMtimeMs": os.path.getmtime(os.path.join(src, "plugin.js")) * 1000,
    "linked": True,
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
PY
say "materialised the desktop half at $DESKTOP"

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable stash >/dev/null 2>&1 && say "enabled plugin (agent tools: stash_add, stash_list, stash_update)" \
    || say "could not auto-enable — run: hermes plugins enable stash"
else
  say "hermes CLI not on PATH — enable it later with: hermes plugins enable stash"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files 2>/dev/null | grep -q '^default-dashboard.service'; then
  systemctl --user restart default-dashboard.service 2>/dev/null && say "restarted the dashboard so the API mounts" || true
fi

cat <<'DONE'

Done. One click left: open Capabilities -> Plugins, find Stash, and enable it.
A package's desktop half ships off on purpose, so the sidebar row appears only
after that toggle. Items live in ~/.hermes/stash/items.json.
DONE
