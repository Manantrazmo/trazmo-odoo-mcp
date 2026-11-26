#!/usr/bin/env bash
# Stop the auto-started MCP and remove its launch agent.
set -euo pipefail
LABEL=com.trazmo.odoo-mcp; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"; echo "Removed $LABEL"
