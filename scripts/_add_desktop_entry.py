"""Adds the trazmo-odoo stdio server to a Claude Desktop config file (used by add_to_claude_desktop.sh)."""
import json
import os

path = os.environ["CFG"]
with open(path) as f:
    cfg = json.load(f)
env = {"ODOO_LOGIN": os.environ["LOGIN"]}
if os.environ.get("ALLOW") == "1":
    env["ODOO_ALLOW_WRITABLE_USER"] = "1"
cfg.setdefault("mcpServers", {})["trazmo-odoo"] = {"command": os.environ["ROOT"] + "/scripts/run_stdio.sh", "env": env}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("Added trazmo-odoo. Servers now:", sorted(cfg["mcpServers"]))
