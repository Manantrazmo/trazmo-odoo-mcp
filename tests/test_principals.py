import json

import pytest
from starlette.testclient import TestClient

from conftest import TODAY, gb
from test_http import FULL, H, LIST, TOKEN, P, auth, settings
from trazmo_odoo_mcp import principals_cli
from trazmo_odoo_mcp.principals import hash_token, parse_principals
from trazmo_odoo_mcp.server import create_app

DIR_TOKEN, HR_TOKEN = "director-token-3", "hr-token-1"
DIRECTOR = {"name": "director-tamkeen", "token_sha256": hash_token(DIR_TOKEN), "tools": ["finance"], "companies": [3]}
HRUSER = {"name": "hr-lead", "token_sha256": hash_token(HR_TOKEN), "tools": ["hr"], "companies": [1, 3]}


@pytest.fixture
def web(client, fake):
    with TestClient(create_app(settings(limit=10_000, extra=(DIRECTOR, HRUSER)), client, lambda: TODAY)) as tc:
        yield tc


def rpc(tc, tok, method, params=None):
    return tc.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                   headers=auth(tok)).json()


def call(tc, tok, name, **args):
    r = rpc(tc, tok, "tools/call", {"name": name, "arguments": args})["result"]
    return r["isError"], r["content"][0]["text"]


def names(tc, tok):
    return {t["name"] for t in rpc(tc, tok, "tools/list")["result"]["tools"]}


def test_tools_list_filtered_per_login(web):
    assert names(web, DIR_TOKEN) == {"list_companies", "get_invoice_summary", "get_cash_positions",
                                     "get_cash_flow_by_month", "get_ytd_mtd", "get_expense_breakdown"}
    assert names(web, HR_TOKEN) == {"list_companies", "get_hr_summary"}
    assert len(names(web, TOKEN)) == 13


def test_director_limited_to_assigned_company(web, fake):
    fake.on(gb("account.move", "move_type"), [])
    assert call(web, DIR_TOKEN, "get_invoice_summary", company_id=3)[0] is False
    for other in (1, 2):
        err, text = call(web, DIR_TOKEN, "get_invoice_summary", company_id=other)
        assert err and "Not permitted" in text
    # nothing for companies 1/2 ever reached Odoo for the director
    assert all(("company_id", "=", 3) in c[2][0] for c in fake.calls if c[0] == "account.move")


def test_forbidden_tool_denied_even_if_called_directly(web):
    err, text = call(web, DIR_TOKEN, "get_hr_summary", company_id=3)
    assert err and "Not permitted" in text
    err, text = call(web, HR_TOKEN, "get_cash_positions", company_id=1)
    assert err and "Not permitted" in text


def test_list_companies_filtered(web):
    ids = lambda tok: [c["id"] for c in json.loads(call(web, tok, "list_companies")[1])["companies"]]  # noqa: E731
    assert ids(DIR_TOKEN) == [3] and ids(HR_TOKEN) == [1, 3] and sorted(ids(TOKEN)) == [1, 2, 3]


def test_audit_log_names_the_principal(web, capsys):
    call(web, DIR_TOKEN, "get_invoice_summary", company_id=1)  # denied
    entries = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if '"audit"' in ln]
    assert entries[-1]["principal"] == "director-tamkeen" and entries[-1]["ok"] is False
    assert DIR_TOKEN not in json.dumps(entries)


def test_rate_limit_is_per_principal(client):
    with TestClient(create_app(settings(limit=2, extra=(DIRECTOR,)), client, lambda: TODAY)) as tc:
        codes = [tc.post("/mcp", json=LIST, headers=auth()).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert tc.post("/mcp", json=LIST, headers=auth(DIR_TOKEN)).status_code == 200  # unaffected


def test_unknown_or_revoked_token_401(web):
    assert web.post("/mcp", json=LIST, headers=auth("never-issued")).status_code == 401


@pytest.mark.parametrize("bad", [
    "not json", "[]", '[{"name":"a"}]',
    json.dumps([{"name": "a", "token_sha256": "x", "tools": "*", "companies": "*"}]),
    json.dumps([{"name": "a", "token_sha256": "0" * 64, "tools": ["nope"], "companies": "*"}]),
    json.dumps([{"name": "a", "token_sha256": "0" * 64, "tools": "*", "companies": [99]}]),
    json.dumps([{"name": "a", "token_sha256": "0" * 64, "tools": [], "companies": "*"}]),
    json.dumps([{"name": "a", "token_sha256": "0" * 64, "tools": "*", "companies": "*"}] * 2),
])
def test_bad_principal_config_refused(bad):
    with pytest.raises(ValueError):
        parse_principals(bad)


def test_cli_add_list_revoke(tmp_path, capsys):
    f = str(tmp_path / "p.json")
    principals_cli.main(["add", "cfo", "--tools", "finance,crm", "--companies", "1,2", "--file", f])
    token = capsys.readouterr().out.strip()
    stored = open(f).read()
    assert token not in stored and hash_token(token) in stored          # only the hash is stored
    (p,) = parse_principals(stored)
    assert p.can_tool("get_crm_summary") and not p.can_tool("get_hr_summary") and p.can_company(2) and not p.can_company(3)
    principals_cli.main(["revoke", "cfo", "--file", f])
    assert json.load(open(f)) == []
