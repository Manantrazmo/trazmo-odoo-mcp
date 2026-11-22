#!/usr/bin/env bash
# Run the MCP on this Mac only (127.0.0.1). Secrets live in the macOS Keychain, never in files or shell history.
#
# One-time setup (you type the values at the prompts; nothing is echoed):
#   security add-generic-password -U -s trazmo-odoo-mcp -a odoo-api-key -w     # the READ-ONLY Odoo user's API key
# Then:
#   ODOO_LOGIN=readonly-user@trazmo.com scripts/run_local.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ODOO_LOGIN:?Set ODOO_LOGIN to the dedicated read-only Odoo user}"
export ODOO_URL="${ODOO_URL:-https://trazmo.odoo.com}" ODOO_DB="${ODOO_DB:-trazmo}" PORT="${PORT:-8765}" HOST=127.0.0.1
export PYTHONPATH=src
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"

if [ -z "${ODOO_API_KEY:-}" ]; then
  ODOO_API_KEY="$(security find-generic-password -s trazmo-odoo-mcp -a odoo-api-key -w 2>/dev/null)" || {
    echo "No Odoo key in Keychain. Run:  security add-generic-password -U -s trazmo-odoo-mcp -a odoo-api-key -w" >&2; exit 1; }
  export ODOO_API_KEY
fi

# Logins live outside the repo. First run creates a 'local' login; its token goes into the Keychain.
CFG="${MCP_LOCAL_CONFIG_DIR:-${HOME}/.config/trazmo-odoo-mcp}"; mkdir -p "$CFG"; chmod 700 "$CFG"; FILE="$CFG/principals.json"
if [ ! -s "$FILE" ]; then
  TOKEN="$("$PY" -m trazmo_odoo_mcp.principals_cli add local --tools '*,ledger,hr' --companies '*' --file "$FILE" 2>/dev/null)"
  security add-generic-password -U -s trazmo-odoo-mcp -a local-token -w "$TOKEN"; unset TOKEN
  chmod 600 "$FILE"; echo "Created 'local' login (token stored in Keychain as trazmo-odoo-mcp / local-token)." >&2
fi
export MCP_PRINCIPALS="$(cat "$FILE")"
echo "Serving http://127.0.0.1:${PORT}/mcp  (Ctrl-C to stop)" >&2
exec "$PY" -m trazmo_odoo_mcp
