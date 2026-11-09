import pytest

from trazmo_odoo_mcp.odoo import MAX_ROWS, MODELS, ReadOnlyViolation

WRITE_METHODS = ["write", "create", "unlink", "copy", "execute", "execute_kw", "action_post",
                 "button_confirm", "action_cancel", "message_post", "name_create", "toggle_active",
                 "read", "search", "browse", "check_access_rights", "sudo", "__init__", "", "SEARCH_READ"]


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_non_allowlisted_methods_rejected(client, fake, method):
    with pytest.raises(ReadOnlyViolation):
        client.call("account.move", method, [[]], {"fields": ["name"]}, 2)
    assert fake.calls == []  # never reached Odoo


@pytest.mark.parametrize("method", ["write", "create", "unlink"])
def test_writes_rejected_on_every_model(client, fake, method):
    for model in MODELS:
        with pytest.raises(ReadOnlyViolation):
            client.call(model, method, [[1], {"name": "x"}], {}, 2)
    assert fake.calls == []


def test_unknown_model_rejected(client, fake):
    for model in ("res.users", "ir.config_parameter", "account.payment", "res.partner.bank"):
        with pytest.raises(ReadOnlyViolation):
            client.search_read(model, [], ["id"])
    assert fake.calls == []


def test_unknown_field_rejected_everywhere(client, fake):
    with pytest.raises(ReadOnlyViolation):
        client.search_read("res.partner", [], ["id", "email"])
    with pytest.raises(ReadOnlyViolation):
        client.search_read("res.partner", [("vat", "=", "x")], ["id"])
    with pytest.raises(ReadOnlyViolation):
        client.search_read("res.partner", [], ["id"], order="phone")
    with pytest.raises(ReadOnlyViolation):
        client.read_group("account.move", [], ["amount_total:sum"], ["partner_id.email"])
    with pytest.raises(ReadOnlyViolation):
        client.read_group("account.move", [], ["amount_total:bogus"], ["move_type"])
    assert fake.calls == []


def test_context_and_extra_kwargs_rejected(client, fake):
    with pytest.raises(ReadOnlyViolation):
        client.call("account.move", "search_read", [[]], {"fields": ["name"], "context": {"x": 1}})
    with pytest.raises(ReadOnlyViolation):
        client.call("account.move", "search_read", [[], 1], {"fields": ["name"]})
    assert fake.calls == []


def test_row_limit_hard_capped(client, fake):
    client.search_read("account.move", [], ["name"], limit=10_000)
    client.search_read("account.move", [], ["name"])
    client.read_group("account.move", [], ["amount_total:sum"], ["move_type"], limit=99999)
    assert [c[3]["limit"] for c in fake.calls] == [MAX_ROWS] * 3


def test_company_scope_forced(client, fake):
    client.search_read("account.move", [("state", "=", "posted")], ["name"], company_id=3)
    model, method, args, kw = fake.calls[0]
    assert args[0][0] == ("company_id", "=", 3) and kw["context"]["allowed_company_ids"] == [3]


def test_fields_get_restricted(client, fake):
    client.fields_get("account.move", ["string", "type"])
    assert "partner_id" in fake.calls[0][2][0]
    with pytest.raises(ReadOnlyViolation):
        client.fields_get("account.move", ["groups"])


def test_negative_limit_clamped(client, fake):
    client.search_read("account.move", [], ["name"], limit=-5)
    assert fake.calls[0][3]["limit"] == 1


def test_http_odoo_url_refused():
    from trazmo_odoo_mcp.odoo import OdooClient
    with pytest.raises(ValueError):
        OdooClient("http://trazmo.odoo.com", "db", "l", "k")
    OdooClient("https://trazmo.odoo.com", "db", "l", "k")


def test_archived_partners_included(client, fake):
    client.search_read("res.partner", [], ["id", "name"])
    assert fake.calls[0][3]["context"]["active_test"] is False


def test_truncation_flag_set_at_row_cap(client, fake):
    from trazmo_odoo_mcp.audit import start_row_counter
    fake.on(lambda *a: True, [{"move_type": "x", "__count": 1}] * MAX_ROWS)
    holder = start_row_counter()
    client.read_group("account.move", [], ["amount_total:sum"], ["move_type"])
    assert holder[1] is True and holder[0] == MAX_ROWS
