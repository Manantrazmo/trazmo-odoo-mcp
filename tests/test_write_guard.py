"""Defence in depth: the server must not write, and must not run with a writable Odoo credential unless told."""
import pytest
from starlette.testclient import TestClient

import fake_odoo
from conftest import TODAY
from test_http import auth, settings
from trazmo_odoo_mcp.odoo import MODELS, OdooClient, OdooError, ReadOnlyViolation


class Spy(OdooClient):
    """Real transport guards, but no network: records what would be sent."""
    def __init__(self, writable=()):
        super().__init__("https://odoo.invalid", "db", "l", "k")
        self.sent, self.writable = [], set(writable)

    def _execute(self, model, method, args, kwargs):
        self.sent.append((model, method))
        if method == "check_access_rights":
            return model in self.writable
        return []


@pytest.mark.parametrize("method", ["write", "create", "unlink", "copy", "action_post", "execute", "check_access_rights",
                                    "button_confirm", "message_post", "load", "import_data", "sudo"])
def test_transport_refuses_every_non_read_method_before_anything_is_sent(method):
    c = Spy()
    with pytest.raises(ReadOnlyViolation):
        c._transport_call("account.move", method, [[1], {"x": 1}], {})
    assert c.sent == []


def test_writable_odoo_user_is_refused_without_the_flag(monkeypatch):
    monkeypatch.delenv("ODOO_ALLOW_WRITABLE_USER", raising=False)
    c = Spy(writable={"crm.lead"})
    with pytest.raises(OdooError, match="can write"):
        c.search_read("account.move", [], ["name"])
    with pytest.raises(OdooError, match="can write"):  # stays refused, no later calls slip through
        c.search_read("account.move", [], ["name"])
    assert not [m for _, m in c.sent if m == "search_read"]


def test_read_only_odoo_user_runs_and_is_probed_once(monkeypatch):
    monkeypatch.delenv("ODOO_ALLOW_WRITABLE_USER", raising=False)
    c = Spy()
    c.search_read("account.move", [], ["name"])
    c.search_read("account.move", [], ["name"])
    probes = [x for x in c.sent if x[1] == "check_access_rights"]
    assert len(probes) == len(MODELS) and [m for _, m in c.sent].count("search_read") == 2


def test_flag_acknowledges_a_writable_user(monkeypatch):
    monkeypatch.setenv("ODOO_ALLOW_WRITABLE_USER", "1")
    c = Spy(writable=set(MODELS))
    c.search_read("account.move", [], ["name"])
    assert [m for _, m in c.sent] == ["search_read"]  # no probe needed; still only reads are sent


def test_end_to_end_refusal_over_real_transport(monkeypatch):
    monkeypatch.delenv("ODOO_ALLOW_WRITABLE_USER", raising=False)
    srv, fake = fake_odoo.start()
    fake.writable = {"crm.lead"}
    client = OdooClient(f"http://127.0.0.1:{srv.server_address[1]}", "trazmo", "ro", fake_odoo.KEY)
    from trazmo_odoo_mcp.server import create_app
    import json
    with TestClient(create_app(settings(limit=10_000), client, lambda: TODAY)) as tc:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_crm_summary", "arguments": {"company_id": 2}}}
        r = tc.post("/mcp", json=body, headers=auth()).json()["result"]
    srv.shutdown()
    assert r["isError"] and "Refusing to run" in r["content"][0]["text"]
    assert all(m != "search_read" for _, m, _ in fake.seen)
