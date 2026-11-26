#!/usr/bin/env bash
# Add the on-demand (stdio) Odoo MCP to Claude Desktop's config. Run it AFTER quitting Claude (Cmd+Q):
# while Claude is running it keeps its own copy of the config and can overwrite this change.
# Usage: ODOO_LOGIN=<login> [ODOO_ALLOW_WRITABLE_USER=1] scripts/add_to_claude_desktop.sh
set -euo pipefail
: "${ODOO_LOGIN:?Set ODOO_LOGIN to the login of the Odoo user}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
if [ -z "${CLAUDE_DESKTOP_CONFIG:-}" ] && ps -axo command | grep -q "[C]laude.app/Contents/MacOS/Claude$"; then
  echo "Claude is still running. Quit it fully (Cmd+Q), then run this again." >&2; exit 1
fi
cp "$CFG" "$CFG.bak-$(date +%Y%m%d-%H%M%S)"
# Desktop may not run files in Downloads, so it launches the copy under ~/.local/share.
[ -n "${CLAUDE_DESKTOP_CONFIG:-}" ] || "$ROOT/scripts/install_local_copy.sh"
CFG="$CFG" ROOT="$HOME/.local/share/trazmo-odoo-mcp" LOGIN="$ODOO_LOGIN" ALLOW="${ODOO_ALLOW_WRITABLE_USER:-}" python3 "$ROOT/scripts/_add_desktop_entry.py"
