#!/usr/bin/env python3
"""Call every tool the token can see, for each company, against the running server; print a pass/fail table.
Usage: scripts/local_check.py [--url http://127.0.0.1:8765/mcp]   (token from $MCP_TOKEN or the Keychain)
A failure like 'Odoo request failed (Fault)' means a field/model name differs in your Odoo version.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

COMPANIES = {1: "Trazmo Pvt Ltd", 2: "Trazmo Holdings", 3: "Tamkeen Financial"}


def token() -> str:
    if os.environ.get("MCP_TOKEN"):
        return os.environ["MCP_TOKEN"]
    r = subprocess.run(["security", "find-generic-password", "-s", "trazmo-odoo-mcp", "-a", "local-token", "-w"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("No token: set MCP_TOKEN or run scripts/run_local.sh once.")
    return r.stdout.strip()


def rpc(url, tok, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}",
                                             "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"http_error": e.code}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    a = ap.parse_args()
    tok = token()
    listed = rpc(a.url, tok, "tools/list")
    if "result" not in listed:
        sys.exit(f"tools/list failed: {listed}")
    tools = [t["name"] for t in listed["result"]["tools"]]
    print(f"{len(tools)} tools visible\n")
    failures = 0
    for name in tools:
        if name == "list_companies":
            continue
        cells = []
        for cid in COMPANIES:
            res = rpc(a.url, tok, "tools/call", {"name": name, "arguments": {"company_id": cid}}).get("result", {})
            ok = res and not res.get("isError")
            failures += not ok
            cells.append("ok" if ok else "FAIL")
            if not ok:
                msg = (res.get("content") or [{"text": "no response"}])[0]["text"][:90]
                print(f"  !! {name} company {cid}: {msg}")
        print(f"{name:28s} " + "  ".join(f"c{cid}:{c:4s}" for cid, c in zip(COMPANIES, cells)))
    print(f"\n{'ALL OK' if not failures else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
