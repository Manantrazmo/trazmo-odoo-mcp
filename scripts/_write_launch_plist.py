"""Writes the launch agent plist from environment variables (used by install_launch_agent.sh). No secrets."""
import os
import plistlib

env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "PORT": os.environ["PORT"],
       "ODOO_LOGIN": os.environ["ODOO_LOGIN"]}
if os.environ.get("ALLOW") == "1":
    env["ODOO_ALLOW_WRITABLE_USER"] = "1"
plist = {"Label": os.environ["LABEL"],
         "ProgramArguments": [os.environ["ROOT"] + "/scripts/run_local.sh"],
         "WorkingDirectory": os.environ["ROOT"], "EnvironmentVariables": env,
         "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
         "StandardOutPath": os.environ["LOG"], "StandardErrorPath": os.environ["LOG"]}
with open(os.environ["PLIST"], "wb") as f:
    plistlib.dump(plist, f)
