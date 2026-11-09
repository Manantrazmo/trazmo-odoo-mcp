from datetime import date

from conftest import TODAY, gb
from trazmo_odoo_mcp import tools as T

M = lambda m, meth: (lambda model, method, a, kw: model == m and method == meth)  # noqa: E731


def has(dom_part):
    return lambda model, method, a, kw: dom_part in map(tuple, [x for x in a[0] if isinstance(x, (list, tuple))])


def test_list_companies():
    ids = {c["id"]: c["currency"] for c in T.list_companies()["companies"]}
    assert ids == {1: "PKR", 2: "USD", 3: "PKR"}


def test_unknown_company_rejected(client):
    import pytest
    with pytest.raises(ValueError):
        T.crm_summary(client, 99)


def test_invoice_summary(client, fake):
    def groups(model, method, a, kw):
        dom = a[0]
        if ("amount_residual", ">", 0) in dom:  # aging buckets: put 100 in every bucket for both sides
            return [{"move_type": "out_invoice", "__count": 1, "amount_residual_signed": 100},
                    {"move_type": "in_invoice", "__count": 2, "amount_residual_signed": -50}]
        return [{"move_type": "out_invoice", "__count": 10, "amount_total_signed": 1000, "amount_residual_signed": 400},
                {"move_type": "in_invoice", "__count": 4, "amount_total_signed": -600, "amount_residual_signed": -150}]
    fake.on(gb("account.move", "move_type"), groups)
    fake.on(M("account.move", "search_read"), [
        {"name": "INV/1", "partner_id": [7, "Jane Doe"], "invoice_date_due": "2026-10-01", "amount_residual_signed": 400},
        {"name": "INV/2", "partner_id": [8, "Acme Ltd"], "invoice_date_due": False, "amount_residual_signed": 10}])
    fake.on(M("res.partner", "search_read"), [
        {"id": 7, "name": "Jane Doe", "is_company": False, "parent_id": False},
        {"id": 8, "name": "Acme Ltd", "is_company": True, "parent_id": False}])
    r = T.invoice_summary(client, 2, TODAY)
    assert r["currency"] == "USD"
    assert r["receivable"] == {"count": 10, "total": 1000.0, "outstanding": 400.0}
    assert r["payable"] == {"count": 4, "total": 600.0, "outstanding": 150.0}
    assert set(r["aging"]["ar"]) == {"current", "1-30", "31-60", "61-90", "90+"}
    assert r["aging"]["ap"]["90+"] == {"amount": 50.0, "count": 2}
    assert r["top_open_receivables"][0]["partner"] == "Individual (name withheld)"
    assert r["top_open_receivables"][1]["partner"] == "Acme Ltd"
    assert "Jane Doe" not in str(r)
    # company scoping applied to every account.move query
    assert all(("company_id", "=", 2) in c[2][0] for c in fake.calls if c[0] == "account.move")


def test_aging_bucket_boundaries(client, fake):
    seen = []
    fake.on(gb("account.move", "move_type"), lambda m, meth, a, kw: seen.append(a[0]) or [])
    T.invoice_summary(client, 1, TODAY)
    flat = str(seen)
    assert "2026-10-10" in flat and "2026-09-10" in flat and "2026-08-11" in flat  # today-30/-60/-90


def test_cash_positions(client, fake):
    fake.on(M("account.journal", "search_read"), [
        {"name": "Bank", "type": "bank", "default_account_id": [10, "1010"], "currency_id": False},
        {"name": "Cash", "type": "cash", "default_account_id": [11, "1011"], "currency_id": False}])
    fake.on(gb("account.move.line", "account_id"), [{"account_id": [10, "1010"], "balance": 1500.456},
                                                    {"account_id": [11, "1011"], "balance": -20}])
    r = T.cash_positions(client, 2, TODAY)
    assert [j["balance"] for j in r["journals"]] == [1500.46, -20.0]
    assert r["total"] == 1480.46
    assert ("parent_state", "=", "posted") in fake.calls[-1][2][0]


def test_cash_flow_by_month(client, fake):
    fake.on(gb("account.move.line", "company_id"),
            [{"company_id": [2, "x"], "debit": 300, "credit": 100, "balance": 200}])
    r = T.cash_flow_by_month(client, 2, TODAY, months=3)
    assert [m["month"] for m in r["months"]] == ["2026-09", "2026-10", "2026-11"]
    assert [m["partial"] for m in r["months"]] == [False, False, True]
    assert r["months"][0]["net"] == 200.0 and r["months"][0]["inflow"] == 300.0
    assert T.cash_flow_by_month(client, 2, TODAY, months=999)["months"].__len__() == 24


def test_ytd_mtd(client, fake):
    def pl(model, method, a, kw):
        types = [x for x in a[0] if isinstance(x, tuple) and x[0] == "account_id.account_type"][0][2]
        if "income" in types:
            return [{"account_id": [1, "Sales"], "balance": -500}, {"account_id": [2, "Fees"], "balance": -100}]
        return [{"account_id": [3, "Rent"], "balance": 200}]
    fake.on(gb("account.move.line", "account_id"), pl)
    r = T.ytd_mtd(client, 2, TODAY)
    assert r["income"]["ytd"] == 600.0 and r["expense"]["ytd"] == 200.0
    assert r["net"]["ytd"] == 400.0
    assert r["income"]["top_accounts_ytd"][0] == {"account": "Sales", "amount": 600.0 - 100.0}
    assert r["comparison_windows"]["prior_year_ytd"] == "2025-01-01..2025-11-09"
    assert r["comparison_windows"]["prior_month_same_days"] == "2026-10-01..2026-10-09"


def test_expense_breakdown_aggregates_people(client, fake):
    accts = [{"account_id": [i, f"A{i}"], "balance": 100 - i} for i in range(12)]
    fake.on(gb("account.move.line", "account_id"), accts)
    fake.on(gb("account.move.line", "partner_id"), [
        {"partner_id": [1, "Vendor Co"], "balance": 500},
        {"partner_id": [2, "Ali Khan"], "balance": 120},
        {"partner_id": [3, "Sara Ahmed"], "balance": 80},
        {"partner_id": [4, "Ali Khan (contact)"], "balance": 10},  # child of Vendor Co
        {"partner_id": [5, "Omar Raza"], "balance": 50},
        {"partner_id": False, "balance": 5}])
    fake.on(M("res.partner", "search_read"), lambda m, meth, a, kw: [
        p for p in [
            {"id": 1, "name": "Vendor Co", "is_company": True, "parent_id": False},
            {"id": 2, "name": "Ali Khan", "is_company": False, "parent_id": False},
            {"id": 3, "name": "Sara Ahmed", "is_company": False, "parent_id": False},
            {"id": 4, "name": "Contact", "is_company": False, "parent_id": [1, "Vendor Co"]},
            {"id": 5, "name": "Omar Raza", "is_company": False, "parent_id": False}]
        if p["id"] in a[0][0][2]])
    r = T.expense_breakdown(client, 2, TODAY)
    assert len(r["by_account"]) == 9 and r["by_account"][-1]["account"] == "Other"
    vendors = {v["vendor"]: v["amount"] for v in r["by_vendor"]}
    assert vendors["Vendor Co"] == 510.0
    assert vendors["Staff & individual reimbursements (3 people, aggregated)"] == 250.0
    blob = str(r)
    assert "Ali Khan" not in blob and "Sara Ahmed" not in blob and "Omar Raza" not in blob


def test_crm_summary(client, fake):
    fake.on(M("crm.lead", "search_count"), 42)
    def g(model, method, a, kw):
        if ("probability", "=", 100) in a[0]:
            return [{"stage_id": [9, "Won"], "__count": 3, "expected_revenue": 900}]
        return [{"stage_id": [1, "New"], "__count": 5, "expected_revenue": 100},
                {"stage_id": [2, "Proposal"], "__count": 2, "expected_revenue": 400}]
    fake.on(gb("crm.lead", "stage_id"), g)
    r = T.crm_summary(client, 1)
    assert (r["total_leads"], r["open"], r["pipeline_value"]) == (42, 7, 500.0)
    assert r["won"] == {"count": 3, "value": 900.0} and len(r["stages"]) == 2


def test_sales_hr_project_loan(client, fake):
    fake.on(gb("sale.order", "state"), [{"state": "sale", "__count": 4, "amount_total": 10},
                                        {"state": "draft", "__count": 1, "amount_total": 5}])
    assert T.sales_summary(client, 1)["order_count"] == 5
    fake.on(gb("hr.employee", "department_id"), [{"department_id": [1, "Eng"], "__count": 6},
                                                 {"department_id": False, "__count": 1}])
    fake.on(M("hr.leave", "search_count"), 3)
    h = T.hr_summary(client, 1)
    assert h["active_headcount"] == 7 and h["pending_leaves"] == 3
    assert h["by_department"][1]["department"] == "No department"
    fake.on(lambda m, meth, a, kw: m == "project.task" and meth == "search_count" and a[0] != [("company_id", "=", 1)],
            2)
    fake.on(M("project.task", "search_count"), 9)
    p = T.project_summary(client, 1, TODAY)
    assert p["tasks_total"] == 9 and p["tasks_overdue"] == 2
    fake.on(gb("account.loan", "state"), [{"state": "posted", "__count": 2}])
    assert T.loan_summary(client, 3)["loan_count"] == 2


def test_mailings_social_unscoped(client, fake):
    fake.on(gb("mailing.mailing", "state"), [{"state": "done", "__count": 8}])
    fake.on(gb("social.post", "state"), [{"state": "posted", "__count": 3}])
    m, s = T.mailings(client, 2), T.social(client, 2)
    assert m["count"] == 8 and s["count"] == 3 and m["company_scoped"] is False


def _expense(client, fake, partners, balances):
    fake.on(gb("account.move.line", "account_id"), [{"account_id": [1, "A"], "balance": 1}])
    fake.on(gb("account.move.line", "partner_id"), balances)
    fake.on(M("res.partner", "search_read"), lambda m, meth, a, kw: [p for p in partners if p["id"] in a[0][0][2]])
    return T.expense_breakdown(client, 2, TODAY)


def test_fewer_than_three_people_not_disclosed(client, fake):
    # 1 company + 1 person = 2 contributors < 3: nothing safe to merge into -> suppress view and total
    r = _expense(client, fake,
                 [{"id": 1, "name": "Co", "is_company": True, "parent_id": False},
                  {"id": 2, "name": "Solo Person", "is_company": False, "parent_id": False}],
                 [{"partner_id": [1, "Co"], "balance": 100}, {"partner_id": [2, "Solo Person"], "balance": 40}])
    assert r["by_vendor"] == [] and r["total"] is None and r["by_account"] == []
    assert "Solo Person" not in str(r) and "includes_withheld" not in str(r)


def test_small_group_pulls_smallest_vendors_into_other(client, fake):
    cos = [{"id": i, "name": f"Co{i}", "is_company": True, "parent_id": False} for i in (1, 2)]
    solo = {"id": 9, "name": "Solo Person", "is_company": False, "parent_id": False}
    bal = [{"partner_id": [1, "Co1"], "balance": 100}, {"partner_id": [2, "Co2"], "balance": 50},
           {"partner_id": [9, "Solo Person"], "balance": 40}]
    r = _expense(client, fake, cos + [solo], bal)
    # 1 person + pulls Co2 (and Co1) until >= 3 contributors: a single "Other vendors" line, no named lines
    assert [v["vendor"] for v in r["by_vendor"]] == ["Other vendors"]
    assert r["by_vendor"][0]["amount"] == 190.0 and r["total"] is not None


def test_three_people_aggregated(client, fake):
    people = [{"id": i, "name": f"P{i}", "is_company": False, "parent_id": False} for i in (2, 3, 4)]
    r = _expense(client, fake, people, [{"partner_id": [i, f"P{i}"], "balance": 10} for i in (2, 3, 4)])
    assert r["by_vendor"] == [{"vendor": "Staff & individual reimbursements (3 people, aggregated)", "amount": 30.0}]


def test_lead_funnel(client, fake):
    counts = {"probability=100": 4, "type": 30}
    def sc(model, method, a, kw):
        d = a[0]
        if ("probability", "=", 100) in d: return 4
        if ("type", "=", "opportunity") in d: return 30
        if ("active", "=", False) in d and ("probability", "=", 0) in d: return 11
        if ("probability", "<", 100) in d: return 25
        return 100
    fake.on(M("crm.lead", "search_count"), sc)
    fake.on(gb("crm.lead", "stage_id"), [{"stage_id": [1, "New"], "__count": 20, "expected_revenue": 500}])
    fake.on(gb("crm.lead", "lost_reason_id"), [{"lost_reason_id": [1, "Price"], "__count": 3},
                                               {"lost_reason_id": False, "__count": 8}])
    r = T.lead_funnel(client, 2, TODAY, days=30)
    assert (r["created"], r["qualified"], r["open"], r["won"], r["lost"]) == (100, 30, 25, 4, 11)
    assert r["win_pct"] == 4.0 and r["qualified_pct"] == 30.0 and r["since"] == "2026-10-10"
    assert r["top_lost_reasons"][0] == {"reason": "Unspecified", "count": 8}
    # archived (lost) leads are included explicitly, window and company applied
    d = fake.calls[0][2][0]
    assert ("active", "in", [True, False]) in d and ("create_date", ">=", "2026-10-10") in d and ("company_id", "=", 2) in d
    assert T.lead_funnel(client, 2, TODAY, days=99999)["window_days"] == 365


def test_leads_by_source(client, fake):
    fake.on(gb("crm.lead", "source_id"), lambda m, meth, a, kw: (
        [{"source_id": [1, "Website"], "__count": 3}] if ("probability", "=", 100) in a[0]
        else [{"source_id": [1, "Website"], "__count": 12}, {"source_id": [2, "Referral"], "__count": 4}]))
    fake.on(gb("crm.lead", "medium_id"), [{"medium_id": False, "__count": 5}])
    fake.on(gb("crm.lead", "campaign_id"), [])
    r = T.leads_by_source(client, 2, TODAY)
    assert r["by_source"][0] == {"name": "Website", "count": 12, "won": 3, "win_pct": 25.0}
    assert r["by_source"][1]["won"] == 0 and r["by_medium"][0]["name"] == "(none)"


def test_lead_response_time(client, fake):
    fake.on(gb("crm.lead", "company_id"), [{"company_id": [2, "x"], "__count": 9, "day_open": 1.256}])
    fake.on(M("crm.lead", "search_count"), lambda m, meth, a, kw: 2 if ("create_date", "<", "2026-11-07") in a[0] else 6)
    r = T.lead_response_time(client, 2, TODAY)
    assert r["avg_days_to_assign"] == 1.26 and r["open_unassigned"] == 6 and r["open_unassigned_older_than_2_days"] == 2


def test_lead_tools_never_read_contact_fields():
    from trazmo_odoo_mcp.odoo import MODELS
    assert not {"email_from", "phone", "mobile", "contact_name", "partner_name", "description", "name"} & MODELS["crm.lead"].fields


# ---- ledger transactions ----------------------------------------------------
import pytest  # noqa: E402

AML = lambda m, meth, a, kw: m == "account.move.line"  # noqa: E731


def _rows(n):
    return [{"id": i, "date": "2026-11-01", "move_name": f"E{i}", "journal_id": [1, "Bank"],
             "account_id": [3, "Rent"], "partner_id": [1 if i % 2 else 2, "x"], "debit": 10.0, "credit": 0.0}
            for i in range(n)]


def _partners(fake):
    fake.on(M("res.partner", "search_read"), lambda m, meth, a, kw: [
        p for p in [{"id": 1, "name": "Acme Ltd", "is_company": True, "parent_id": False},
                    {"id": 2, "name": "Ali Khan", "is_company": False, "parent_id": False}]
        if any(leaf[0] == "id" and p["id"] in leaf[2] for leaf in a[0] if isinstance(leaf, tuple))
        or any(leaf == ("is_company", "=", True) for leaf in a[0])])


def test_search_transactions_masks_individuals_and_paginates(client, fake):
    _partners(fake)
    fake.on(lambda m, meth, a, kw: m == "account.move.line" and meth == "search_read", _rows(3))
    fake.on(gb("account.move.line", "company_id"), [{"company_id": [2, "x"], "__count": 3, "debit": 30, "credit": 0}])
    r = T.search_transactions(client, 2, TODAY, limit=2)
    assert len(r["transactions"]) == 2 and r["has_more"] is True and r["matched"] == {"count": 3, "debit": 30.0, "credit": 0.0}
    cps = {t["counterparty"] for t in r["transactions"]}
    assert cps == {"Acme Ltd", "Individual (name withheld)"} and "Ali Khan" not in str(r)
    call = [c for c in fake.calls if c[0] == "account.move.line" and c[1] == "search_read"][0]
    assert call[3]["limit"] == 3 and call[3]["order"] == "date desc, id desc"
    assert ("parent_state", "=", "posted") in call[2][0] and ("company_id", "=", 2) in call[2][0]


def test_search_transactions_limits_and_validation(client, fake):
    _partners(fake)
    T.search_transactions(client, 2, TODAY, limit=100000)
    sr = [c for c in fake.calls if c[0] == "account.move.line" and c[1] == "search_read"][0]
    assert sr[3]["limit"] == T.TX_MAX_ROWS + 1
    for bad in (dict(date_from="2024-01-01"), dict(date_from="2026-12-01", date_to="2026-11-01"),
                dict(date_from="nope"), dict(account_type="; drop"), dict(journal_type="x")):
        with pytest.raises(ValueError):
            T.search_transactions(client, 2, TODAY, **bad)


def test_counterparty_search_only_matches_companies(client, fake):
    _partners(fake)
    fake.on(AML, [])
    T.search_transactions(client, 2, TODAY, counterparty_search="ali")
    ps = [c for c in fake.calls if c[0] == "res.partner"][0]
    assert ("is_company", "=", True) in ps[2][0]  # a person's name can never be probed


def test_amount_and_account_filters_in_domain(client, fake):
    _partners(fake)
    T.search_transactions(client, 2, TODAY, account_search="6000", min_amount=100, max_amount=-500, account_type="expense")
    d = [c for c in fake.calls if c[1] == "search_read" and c[0] == "account.move.line"][0][2][0]
    assert ("account_id.code", "=like", "6000%") in d and ("debit", "<=", 500.0) in d
    assert ("account_id.account_type", "=", "expense") in d


def test_transaction_totals_by_account_and_partner(client, fake):
    fake.on(gb("account.move.line", "account_id"), [{"account_id": [i, f"A{i}"], "__count": 1, "debit": 10, "credit": 0, "balance": 30 - i} for i in range(28)])
    r = T.transaction_totals(client, 2, TODAY, "account")
    assert len(r["groups"]) == 26 and r["groups"][-1]["name"] == "Other" and r["groups"][-1]["count"] == 3
    with pytest.raises(ValueError):
        T.transaction_totals(client, 2, TODAY, "memo")
    _partners(fake)
    fake.on(gb("account.move.line", "partner_id"), [{"partner_id": [1, "Acme Ltd"], "balance": 100},
                                                    {"partner_id": [2, "Ali Khan"], "balance": 40}])
    r = T.transaction_totals(client, 2, TODAY, "partner")  # default: this month to date
    assert r["from"] == "2026-11-01" and r["groups"] == [] and "withheld" in r["note"] and "Ali Khan" not in str(r)


def test_ledger_never_reads_memo_or_bank_fields():
    from trazmo_odoo_mcp.odoo import MODELS
    assert not {"name", "ref", "narration", "partner_bank_id", "payment_ref"} & MODELS["account.move.line"].fields


@pytest.mark.parametrize("kw", [dict(account_search="rent"), dict(journal_type="bank"), dict(account_type="asset_cash"),
                                dict(date_from="2026-10-05", date_to="2026-10-31"),
                                dict(date_from="2026-10-01", date_to="2026-10-20")])
def test_partner_totals_reject_narrowing_filters_and_partial_months(client, fake, kw):
    with pytest.raises(ValueError):
        T.transaction_totals(client, 2, TODAY, "partner", **kw)


def test_partner_totals_accept_whole_months(client, fake):
    fake.on(gb("account.move.line", "partner_id"), [])
    assert T.transaction_totals(client, 2, TODAY, "partner", date_from="2026-09-01", date_to="2026-10-31",
                                account_type="expense")["groups"] == []
    assert T.transaction_totals(client, 2, TODAY, "partner", date_from="2026-11-01", date_to="2026-11-09")
