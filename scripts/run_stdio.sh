#!/usr/bin/env bash
# Launched by Claude Code / Claude Desktop as a child process (starts when a session uses it, exits with it).
# Secrets come from the macOS Keychain. Requires ODOO_LOGIN in the environment (set it via `claude mcp add -e`).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ODOO_LOGIN:?Set ODOO_LOGIN (claude mcp add ... -e ODOO_LOGIN=...)}"
export ODOO_URL="${ODOO_URL:-https://trazmo.odoo.com}" ODOO_DB="${ODOO_DB:-trazmo}" PYTHONPATH=src
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
if [ -z "${ODOO_API_KEY:-}" ]; then
  ODOO_API_KEY="$(security find-generic-password -s trazmo-odoo-mcp -a odoo-api-key -w)" || {
    echo "No Odoo key in Keychain (see README)" >&2; exit 1; }
  export ODOO_API_KEY
fi
FILE="${MCP_LOCAL_CONFIG_DIR:-${HOME}/.config/trazmo-odoo-mcp}/principals.json"
[ -s "$FILE" ] || { echo "No logins file: run scripts/run_local.sh once to create it" >&2; exit 1; }
export MCP_PRINCIPALS="$(cat "$FILE")"
exec "$PY" -m trazmo_odoo_mcp --stdio
