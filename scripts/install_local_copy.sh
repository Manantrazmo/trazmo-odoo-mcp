#!/usr/bin/env bash
# Copy the server to ~/.local/share/trazmo-odoo-mcp and build its Python environment there.
# macOS blocks background jobs and apps (launchd, Claude Desktop) from running files in protected folders such as
# Downloads, Documents and Desktop, so they run this copy. Re-run after code changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/.local/share/trazmo-odoo-mcp"
mkdir -p "$APP/scripts"
rm -rf "$APP/src"; cp -R "$ROOT/src" "$APP/src"
cp "$ROOT/scripts/run_local.sh" "$ROOT/scripts/run_stdio.sh" "$APP/scripts/"
cp "$ROOT/requirements.txt" "$APP/requirements.txt"
UV="$(command -v uv || true)"
if [ ! -x "$APP/.venv/bin/python" ]; then
  if [ -n "$UV" ]; then "$UV" venv --python 3.12 "$APP/.venv" -q
  else "$(command -v python3.12 || command -v python3)" -m venv "$APP/.venv"; fi
fi
if [ -n "$UV" ]; then "$UV" pip install --python "$APP/.venv/bin/python" -q -r "$APP/requirements.txt"
else "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"; fi
echo "Local copy ready: $APP"
