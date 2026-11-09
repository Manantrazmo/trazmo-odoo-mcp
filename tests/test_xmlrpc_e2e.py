"""HTTP -> MCP tool -> OdooClient -> real XML-RPC -> fake Odoo. Covers transport, auth, threads, scoping."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

import fake_odoo
from conftest import TODAY
from test_http import EXPECTED, TOKEN, auth, settings
from trazmo_odoo_mcp.odoo import OdooClient
from trazmo_odoo_mcp.server import create_app

ARGS = {"get_cash_flow_by_month": {"months": 3}, "get_expense_breakdown": {"months": 3}}


@pytest.fixture
def stack():
    srv, fake = fake_odoo.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    client = OdooClient(url, "trazmo", "ro", fake_odoo.KEY)
    with TestClient(create_app(settings(limit=10_000), client, lambda: TODAY)) as tc:
        yield tc, fake
    srv.shutdown()


def call(tc, name, **args):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    r = tc.post("/mcp", json=body, headers=auth()).json()["result"]
    return r["isError"], (json.loads(r["content"][0]["text"]) if not r["isError"] else r["content"][0]["text"])


@pytest.mark.parametrize("company_id", [1, 2, 3])
def test_every_tool_runs_for_each_company(stack, company_id):
    tc, fake = stack
    for name in sorted(EXPECTED - {"list_companies"}):
        err, out = call(tc, name, company_id=company_id, **ARGS.get(name, {}))
        assert not err, (name, out)
        assert "truncated" not in out
    from trazmo_odoo_mcp.odoo import MODELS
    scoped = [c for c in fake.seen if MODELS[c[0]].company_field]
    assert scoped and all(c[2]["allowed_company_ids"] == [company_id] for c in scoped)
    assert all(m in fake_odoo.READS for _, m, _ in fake.seen)


def test_values_and_privacy_over_real_transport(stack):
    tc, _ = stack
    _, inv = call(tc, "get_invoice_summary", company_id=2)
    assert inv["receivable"] == {"count": 12, "total": 5000.0, "outstanding": 1500.5}
    assert inv["payable"]["outstanding"] == 800.0
    assert inv["top_open_receivables"][1]["partner"] == "Individual (name withheld)"
    _, cash = call(tc, "get_cash_positions", company_id=2)
    assert [j["balance"] for j in cash["journals"]] == [9000.0, 250.0]
    _, exp = call(tc, "get_expense_breakdown", company_id=2)
    vendors = {v["vendor"]: v["amount"] for v in exp["by_vendor"]}
    assert vendors["Acme Supplies Ltd"] == 510.0  # child contact rolled into parent company
    assert vendors["Staff & individual reimbursements (3 people, aggregated)"] == 260.0
    blob = json.dumps([inv, exp])
    assert not any(n in blob for n in ("Ali Khan", "Sara Ahmed", "Omar Raza"))


def test_concurrent_calls_all_succeed(stack):
    tc, _ = stack
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda i: call(tc, "get_invoice_summary", company_id=1 + i % 3), range(48)))
    assert all(not err for err, _ in results)


def test_bad_odoo_key_gives_sanitised_error():
    srv, _ = fake_odoo.start()
    bad = OdooClient(f"http://127.0.0.1:{srv.server_address[1]}", "trazmo", "ro", "WRONG-KEY-123")
    with TestClient(create_app(settings(), bad, lambda: TODAY)) as tc:
        err, text = call(tc, "get_crm_summary", company_id=2)
    srv.shutdown()
    assert err and "WRONG-KEY-123" not in text and "denied" not in text
