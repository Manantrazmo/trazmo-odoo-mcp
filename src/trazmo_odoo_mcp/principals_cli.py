"""Manage logins. Usage (stdlib only):
  python -m trazmo_odoo_mcp.principals_cli add NAME --tools finance,crm --companies 3 [--project P]
  python -m trazmo_odoo_mcp.principals_cli revoke NAME [--project P]
  python -m trazmo_odoo_mcp.principals_cli list [--project P]
Add --file PATH to operate on a local JSON file instead of Secret Manager (for testing).
`add` prints the new token ONCE on stdout (hand it to its owner); only its hash is stored.
"""
import argparse
import json
import secrets
import subprocess
import sys

from .principals import hash_token, parse_principals

SECRET = "MCP_PRINCIPALS"


def _gcloud(args, stdin=None, project=None):
    cmd = ["gcloud", *args] + (["--project", project] if project else [])
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def load(a) -> list:
    if a.file:
        try:
            return json.load(open(a.file))
        except FileNotFoundError:
            return []
    r = _gcloud(["secrets", "versions", "access", "latest", f"--secret={SECRET}"], project=a.project)
    return json.loads(r.stdout) if r.returncode == 0 else []


def save(a, entries: list) -> None:
    text = json.dumps(entries, indent=2)
    if entries:
        parse_principals(text)  # validate before storing
    if a.file:
        open(a.file, "w").write(text)
        return
    exists = _gcloud(["secrets", "describe", SECRET], project=a.project).returncode == 0
    verb = ["secrets", "versions", "add", SECRET] if exists else ["secrets", "create", SECRET, "--replication-policy=automatic"]
    r = _gcloud(verb + ["--data-file=-"], stdin=text, project=a.project)
    if r.returncode != 0:
        sys.exit("Failed to store secret: " + r.stderr.strip().splitlines()[-1])
    print("Stored. Running instances read the secret at start-up: deploy a new revision to apply it, e.g.\n"
          "  gcloud run services update trazmo-odoo-mcp --region <region> --update-env-vars PRINCIPALS_REV=$(date +%s)",
          file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="principals_cli")
    ap.add_argument("cmd", choices=["add", "revoke", "list"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--tools", default="")
    ap.add_argument("--companies", default="")
    ap.add_argument("--project")
    ap.add_argument("--file")
    a = ap.parse_args(argv)
    entries = load(a)
    if a.cmd == "list":
        for e in entries:
            print(e["name"], "tools=", e["tools"], "companies=", e["companies"])
        return
    if not a.name:
        sys.exit("name required")
    if a.cmd == "revoke":
        if a.name not in {e["name"] for e in entries}:
            sys.exit("no such principal")
        save(a, [e for e in entries if e["name"] != a.name])
        return
    if a.name in {e["name"] for e in entries}:
        sys.exit("principal already exists (revoke it first to reissue)")
    tools = "*" if a.tools == "*" else [t for t in a.tools.split(",") if t]
    companies = "*" if a.companies == "*" else [int(c) for c in a.companies.split(",") if c]
    token = secrets.token_urlsafe(48)
    save(a, entries + [{"name": a.name, "token_sha256": hash_token(token), "tools": tools, "companies": companies}])
    print(token)


if __name__ == "__main__":
    main()
