import json

import pytest
from starlette.testclient import TestClient

from conftest import TODAY, gb
from trazmo_odoo_mcp.config import Settings
from trazmo_odoo_mcp.principals import hash_token, parse_principals
from trazmo_odoo_mcp.server import create_app

TOKEN = "test-token-abc123"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

EXPECTED = {"list_companies", "get_invoice_summary", "get_cash_positions", "get_cash_flow_by_month",
            "get_ytd_mtd", "get_expense_breakdown", "get_crm_summary", "get_sales_summary", "get_hr_summary",
            "get_project_summary", "get_loan_summary", "get_mailings", "get_social",
            "get_lead_funnel", "get_leads_by_source", "get_lead_response_time",
            "search_transactions", "get_transaction_totals"}


def P(*entries):
    return parse_principals(json.dumps(list(entries)))


FULL = {"name": "admin", "token_sha256": hash_token(TOKEN), "tools": "*", "companies": "*"}


def settings(limit=60, extra=()):
    return Settings("https://odoo.invalid", "db", "login", "SECRET-KEY", P(FULL, *extra), limit)


@pytest.fixture
def web(client, fake):
    with TestClient(create_app(settings(), client, lambda: TODAY)) as tc:
        yield tc


def auth(tok=TOKEN):
    return {**H, "Authorization": f"Bearer {tok}"}


def test_healthz_open_and_plain(web):
    r = web.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


@pytest.mark.parametrize("headers", [
    H,                                                   # missing
    {**H, "Authorization": "Bearer wrong"},              # wrong
    {**H, "Authorization": "Bearer "},                   # empty
    {**H, "Authorization": TOKEN},                       # no scheme
    {**H, "Authorization": f"Basic {TOKEN}"},            # wrong scheme
    {**H, "Authorization": f"Bearer {TOKEN}x"},          # near miss
])
def test_bad_auth_is_401_without_detail(web, headers):
    r = web.post("/mcp", json=LIST, headers=headers)
    assert r.status_code == 401 and r.text == ""


def test_token_in_url_not_accepted(web):
    assert web.post(f"/mcp?token={TOKEN}", json=LIST, headers=H).status_code == 401


def test_tools_list_with_token(web):
    r = web.post("/mcp", json=LIST, headers=auth())
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == EXPECTED
    assert not any(n.startswith(("write", "create", "unlink", "execute")) for n in names)


def test_tool_call_end_to_end_and_audit_log(web, fake, capsys):
    fake.on(gb("crm.lead", "stage_id"), [{"stage_id": [1, "New"], "__count": 2, "expected_revenue": 50}])
    fake.on(lambda m, meth, a, kw: m == "crm.lead" and meth == "search_count", 5)
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_crm_summary", "arguments": {"company_id": 2}}}
    r = web.post("/mcp", json=call, headers=auth())
    body = r.json()["result"]
    assert not body.get("isError")
    assert json.loads(body["content"][0]["text"])["total_leads"] == 5
    out = capsys.readouterr().out
    entry = json.loads([ln for ln in out.splitlines() if '"audit"' in ln][-1])
    assert entry["tool"] == "get_crm_summary" and entry["company_id"] == 2 and entry["row_count"] >= 1
    for secret in (TOKEN, "SECRET-KEY"):
        assert secret not in out
    assert "expected_revenue" not in out  # no payloads


def test_invalid_company_is_tool_error(web):
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_crm_summary", "arguments": {"company_id": 99}}}
    assert web.post("/mcp", json=call, headers=auth()).json()["result"]["isError"] is True


def test_no_write_tool_exposed(web):
    call = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "write", "arguments": {"model": "account.move"}}}
    r = web.post("/mcp", json=call, headers=auth()).json()
    assert "error" in r or r["result"]["isError"]


def test_rate_limit(client):
    with TestClient(create_app(settings(limit=2), client, lambda: TODAY)) as tc:
        codes = [tc.post("/mcp", json=LIST, headers=auth()).status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and codes[2:] == [429, 429]
        assert tc.get("/healthz").status_code == 200  # health not limited


def test_mcp_trailing_slash_and_other_paths_404_no_redirect(web):
    for path in ("/mcp/", "/MCP", "/other"):
        r = web.post(path, json=LIST, headers=auth(), follow_redirects=False)
        assert r.status_code == 404 and "location" not in r.headers


def test_unauthenticated_flood_is_limited(client):
    from trazmo_odoo_mcp.security import GuardMiddleware, RateLimiter
    async def inner(scope, receive, send):  # http never reaches it
        assert scope["type"] == "lifespan"
        while True:
            m = await receive()
            if m["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:
                await send({"type": "lifespan.shutdown.complete"})
                return
    g = GuardMiddleware(inner, P(FULL), 60, RateLimiter(3))
    with TestClient(g) as tc:
        codes = [tc.post("/mcp", json=LIST, headers=H).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_websocket_scope_fails_closed():
    import asyncio
    from trazmo_odoo_mcp.security import GuardMiddleware, RateLimiter
    called = []
    async def inner(scope, receive, send):
        called.append(1)
    g = GuardMiddleware(inner, P(FULL), 60)
    asyncio.run(g({"type": "websocket", "path": "/mcp", "headers": []}, None, None))
    assert called == []
