#!/usr/bin/env bash
# Start the local MCP automatically at login (and restart it if it stops), with no command to type.
# Install:   ODOO_LOGIN=<login> [ODOO_ALLOW_WRITABLE_USER=1] [PORT=8777] scripts/install_launch_agent.sh
# Remove:    scripts/uninstall_launch_agent.sh
# Secrets are NOT written to the plist: the Odoo key and the local token are read from the Keychain at start-up.
# DRY_RUN=1 writes and lints the plist in /tmp without installing anything.
set -euo pipefail
LABEL=com.trazmo.odoo-mcp
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${ODOO_LOGIN:?Set ODOO_LOGIN to the login of the Odoo user}"
PORT="${PORT:-8777}"
LOG="$HOME/Library/Logs/trazmo-odoo-mcp.log"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"; [ "${DRY_RUN:-}" = 1 ] && PLIST="/tmp/$LABEL.plist"
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

ROOT="$ROOT" LABEL="$LABEL" LOG="$LOG" PLIST="$PLIST" PORT="$PORT" ODOO_LOGIN="$ODOO_LOGIN" \
ALLOW="${ODOO_ALLOW_WRITABLE_USER:-}" python3 "$ROOT/scripts/_write_launch_plist.py"
chmod 600 "$PLIST"; plutil -lint "$PLIST"
if [ "${DRY_RUN:-}" = 1 ]; then echo "dry run: $PLIST written and valid (nothing installed)"; exit 0; fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Installed. Serving http://127.0.0.1:${PORT}/mcp (starts at login). Log: $LOG"
