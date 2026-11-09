"""Tool logic. Pure functions of (OdooClient, company_id, today); aggregation is server-side."""
import calendar
from datetime import date, timedelta

from .config import COMPANIES
from .odoo import OdooClient

EXPENSE_TYPES = ["expense", "expense_direct_cost", "expense_depreciation"]
INCOME_TYPES = ["income", "income_other"]
POSTED_LINE = [("parent_state", "=", "posted")]
MIN_PEOPLE_GROUP = 3  # never disclose an aggregate of fewer people
STAFF_LABEL = "Staff & individual reimbursements ({n} people, aggregated)"


# ---- helpers --------------------------------------------------------------
def company(company_id: int) -> dict:
    if company_id not in COMPANIES:
        raise ValueError(f"Unknown company_id. Valid: {sorted(COMPANIES)}")
    return COMPANIES[company_id]


def r2(x) -> float:
    return round(float(x or 0), 2)


def _name(v):
    """many2one read value [id, name] / False -> name."""
    return v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else None


def _id(v):
    return v[0] if isinstance(v, (list, tuple)) and v else None


def first_of_month(d: date, back: int = 0) -> date:
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def same_day_prior_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


def resolve_partners(c: OdooClient, ids: list[int]) -> dict[int, dict]:
    """partner id -> {'key', 'name', 'individual'}. Child contacts roll up to their parent company."""
    out: dict[int, dict] = {}
    if not ids:
        return out
    rows = c.search_read("res.partner", [("id", "in", sorted(set(ids)))],
                         ["id", "name", "is_company", "parent_id"])
    by_id = {r["id"]: r for r in rows}
    parent_ids = sorted({_id(r["parent_id"]) for r in rows if _id(r["parent_id"])} - set(by_id))
    if parent_ids:
        for r in c.search_read("res.partner", [("id", "in", parent_ids)],
                               ["id", "name", "is_company", "parent_id"]):
            by_id[r["id"]] = r
    for pid in ids:
        r = by_id.get(pid)
        if not r:
            out[pid] = {"key": ("unknown", pid), "name": None, "individual": True}
            continue
        parent = by_id.get(_id(r["parent_id"])) if _id(r["parent_id"]) else None
        if parent and parent["is_company"]:
            out[pid] = {"key": ("p", parent["id"]), "name": parent["name"], "individual": False}
        elif r["is_company"]:
            out[pid] = {"key": ("p", r["id"]), "name": r["name"], "individual": False}
        else:
            out[pid] = {"key": ("p", r["id"]), "name": None, "individual": True}
    return out


def _group_sum(rows: list[dict], key: str) -> float:
    return sum(float(r.get(key) or 0) for r in rows)


# ---- 1 ---------------------------------------------------------------------
def list_companies() -> dict:
    return {"companies": list(COMPANIES.values())}


# ---- 2 ---------------------------------------------------------------------
def invoice_summary(c: OdooClient, company_id: int, today: date) -> dict:
    co = company(company_id)
    base = [("move_type", "in", ["out_invoice", "in_invoice"]), ("state", "=", "posted")]
    groups = c.read_group("account.move", base, ["amount_total_signed:sum", "amount_residual_signed:sum"],
                          ["move_type"], company_id)
    by_type = {g["move_type"]: g for g in groups}

    def side(t):
        g = by_type.get(t, {})
        return {"count": int(g.get("__count", 0)),
                "total": r2(abs(g.get("amount_total_signed", 0))),
                "outstanding": r2(abs(g.get("amount_residual_signed", 0)))}

    ar, ap = side("out_invoice"), side("in_invoice")

    d = lambda n: (today - timedelta(days=n)).isoformat()  # noqa: E731
    buckets = {
        "current": ["|", ("invoice_date_due", ">=", today.isoformat()), ("invoice_date_due", "=", False)],
        "1-30": [("invoice_date_due", "<", today.isoformat()), ("invoice_date_due", ">=", d(30))],
        "31-60": [("invoice_date_due", "<", d(30)), ("invoice_date_due", ">=", d(60))],
        "61-90": [("invoice_date_due", "<", d(60)), ("invoice_date_due", ">=", d(90))],
        "90+": [("invoice_date_due", "<", d(90))],
    }
    aging = {"ar": {}, "ap": {}}
    for label, dom in buckets.items():
        rows = c.read_group("account.move", base + [("amount_residual", ">", 0)] + dom,
                            ["amount_residual_signed:sum"], ["move_type"], company_id)
        m = {g["move_type"]: g for g in rows}
        for key, t in (("ar", "out_invoice"), ("ap", "in_invoice")):
            g = m.get(t, {})
            aging[key][label] = {"amount": r2(abs(g.get("amount_residual_signed", 0))),
                                 "count": int(g.get("__count", 0))}

    def top(t):
        rows = c.search_read("account.move", base + [("move_type", "=", t), ("amount_residual", ">", 0)],
                             ["name", "partner_id", "invoice_date_due", "amount_residual_signed"],
                             company_id, order="amount_residual desc", limit=5)
        pmap = resolve_partners(c, [_id(r["partner_id"]) for r in rows if _id(r["partner_id"])])
        items = []
        for r in rows:
            p = pmap.get(_id(r["partner_id"]))
            items.append({
                "reference": r["name"],
                "partner": "Individual (name withheld)" if (p and p["individual"]) else (p["name"] if p else None),
                "due_date": r["invoice_date_due"] or None,
                "residual": r2(abs(r["amount_residual_signed"])),
            })
        return items

    return {"company": co, "as_of": today.isoformat(), "currency": co["currency"],
            "receivable": ar, "payable": ap, "aging": aging,
            "top_open_receivables": top("out_invoice"), "top_open_payables": top("in_invoice")}


# ---- 3 ---------------------------------------------------------------------
def cash_positions(c: OdooClient, company_id: int, today: date) -> dict:
    co = company(company_id)
    journals = c.search_read("account.journal", [("type", "in", ["bank", "cash"])],
                             ["name", "type", "default_account_id", "currency_id"], company_id)
    acc_ids = [_id(j["default_account_id"]) for j in journals if _id(j["default_account_id"])]
    sums = {}
    if acc_ids:
        rows = c.read_group("account.move.line", POSTED_LINE + [("account_id", "in", acc_ids)],
                            ["balance:sum"], ["account_id"], company_id)
        sums = {_id(g["account_id"]): g.get("balance", 0) for g in rows}
    out = [{"journal": j["name"], "type": j["type"],
            "balance": r2(sums.get(_id(j["default_account_id"]), 0))} for j in journals]
    return {"company": co, "as_of": today.isoformat(), "currency": co["currency"], "journals": out,
            "total": r2(sum(x["balance"] for x in out))}


# ---- 4 ---------------------------------------------------------------------
def cash_flow_by_month(c: OdooClient, company_id: int, today: date, months: int = 6) -> dict:
    co = company(company_id)
    months = max(1, min(int(months), 24))
    out = []
    for back in range(months - 1, -1, -1):
        start = first_of_month(today, back)
        end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        dom = POSTED_LINE + [("account_id.account_type", "=", "asset_cash"),
                             ("date", ">=", start.isoformat()), ("date", "<=", end.isoformat())]
        rows = c.read_group("account.move.line", dom, ["debit:sum", "credit:sum", "balance:sum"],
                            ["company_id"], company_id)
        out.append({"month": start.strftime("%Y-%m"),
                    "inflow": r2(_group_sum(rows, "debit")), "outflow": r2(_group_sum(rows, "credit")),
                    "net": r2(_group_sum(rows, "balance")),
                    "partial": (start.year, start.month) == (today.year, today.month)})
    return {"company": co, "currency": co["currency"], "months": out}


# ---- 5 ---------------------------------------------------------------------
def _pl(c, company_id, types, start: date, end: date, sign: int, with_accounts=False):
    dom = POSTED_LINE + [("account_id.account_type", "in", types),
                         ("date", ">=", start.isoformat()), ("date", "<=", end.isoformat())]
    rows = c.read_group("account.move.line", dom, ["balance:sum"], ["account_id"], company_id)
    items = [(_name(g["account_id"]) or "Unknown", sign * float(g.get("balance") or 0)) for g in rows]
    total = r2(sum(v for _, v in items))
    if not with_accounts:
        return total
    top = sorted(items, key=lambda kv: abs(kv[1]), reverse=True)[:8]
    return total, [{"account": n, "amount": r2(v)} for n, v in top]


def ytd_mtd(c: OdooClient, company_id: int, today: date) -> dict:
    co = company(company_id)
    ytd_s, mtd_s = date(today.year, 1, 1), today.replace(day=1)
    py_s, py_e = date(today.year - 1, 1, 1), same_day_prior_year(today)
    pm_s = first_of_month(today, 1)
    pm_last = calendar.monthrange(pm_s.year, pm_s.month)[1]
    pm_e = pm_s.replace(day=min(today.day, pm_last))  # same elapsed days in prior month

    res = {}
    for kind, types, sign in (("income", INCOME_TYPES, -1), ("expense", EXPENSE_TYPES, 1)):
        ytd, top = _pl(c, company_id, types, ytd_s, today, sign, True)
        res[kind] = {
            "ytd": ytd, "mtd": _pl(c, company_id, types, mtd_s, today, sign),
            "prior_year_ytd": _pl(c, company_id, types, py_s, py_e, sign),
            "prior_month_same_days": _pl(c, company_id, types, pm_s, pm_e, sign),
            "top_accounts_ytd": top,
        }
    net = {k: r2(res["income"][k] - res["expense"][k])
           for k in ("ytd", "mtd", "prior_year_ytd", "prior_month_same_days")}
    return {"company": co, "currency": co["currency"], "basis": "P&L, posted entries",
            "as_of": today.isoformat(),
            "comparison_windows": {"prior_year_ytd": f"{py_s}..{py_e}", "prior_month_same_days": f"{pm_s}..{pm_e}"},
            **res, "net": net}


# ---- 6 ---------------------------------------------------------------------
def expense_breakdown(c: OdooClient, company_id: int, today: date, months: int = 6) -> dict:
    co = company(company_id)
    months = max(1, min(int(months), 24))
    start = first_of_month(today, months - 1)
    dom = POSTED_LINE + [("account_id.account_type", "in", EXPENSE_TYPES),
                         ("date", ">=", start.isoformat()), ("date", "<=", today.isoformat())]
    by_acc = c.read_group("account.move.line", dom, ["balance:sum"], ["account_id"], company_id)
    items = sorted(((_name(g["account_id"]) or "Unknown", float(g.get("balance") or 0)) for g in by_acc),
                   key=lambda kv: abs(kv[1]), reverse=True)
    accounts = [{"account": n, "amount": r2(v)} for n, v in items[:8]]
    if len(items) > 8:
        accounts.append({"account": "Other", "amount": r2(sum(v for _, v in items[8:]))})

    by_partner = c.read_group("account.move.line", dom, ["balance:sum"], ["partner_id"], company_id,
                              orderby="balance desc")
    pmap = resolve_partners(c, [_id(g["partner_id"]) for g in by_partner if _id(g["partner_id"])])
    vendors: dict = {}
    people: set = set()
    people_total = 0.0
    no_partner = 0.0
    for g in by_partner:
        pid, amt = _id(g["partner_id"]), float(g.get("balance") or 0)
        if pid is None:
            no_partner += amt
            continue
        p = pmap[pid]
        if p["individual"]:
            people.add(p["key"])
            people_total += amt
        else:
            vendors[p["key"]] = (p["name"], vendors.get(p["key"], (None, 0.0))[1] + amt)
    ranked = sorted(vendors.values(), key=lambda kv: abs(kv[1]), reverse=True)
    out = [{"vendor": n, "amount": r2(v)} for n, v in ranked[:8]]
    other, other_count = sum(v for _, v in ranked[8:]), max(0, len(ranked) - 8)
    withheld = 0.0
    if 0 < len(people) < MIN_PEOPLE_GROUP:  # too few people to aggregate safely
        other, other_count = other + people_total, other_count + len(people)
        people, people_total = set(), 0.0
        # Only show the merged line if it can't isolate a person: needs >= K contributors,
        # or contain no person at all (the else case below).
        if other_count >= MIN_PEOPLE_GROUP:
            out.append({"vendor": "Other vendors", "amount": r2(other)})
        else:
            withheld = other  # fold into the largest vendor line below, so by_vendor still sums to total
    elif other_count:
        out.append({"vendor": "Other vendors", "amount": r2(other)})
    if people:
        out.append({"vendor": STAFF_LABEL.format(n=len(people)), "amount": r2(people_total)})
    if no_partner:
        out.append({"vendor": "(No partner on entry)", "amount": r2(no_partner)})
    total, note = r2(sum(v for _, v in items)), {}
    if withheld:
        if out:  # absorb into the largest company line: no line isolates a person, no differencing gap
            out[0]["amount"] = r2(out[0]["amount"] + withheld)
            out[0]["includes_withheld_small_amounts"] = True
            note = {"vendor_note": "Small individual amounts are included in the line marked "
                                   "includes_withheld_small_amounts."}
        else:  # nothing safe to hide it in: withhold the vendor view and total entirely
            total, note = None, {"vendor_note": "Vendor view and total withheld: too few contributors."}
    return {"company": co, "currency": co["currency"], "from": start.isoformat(), "to": today.isoformat(),
            "total": total, "by_account": accounts if total is not None else [], "by_vendor": out, **note}


# ---- 7-12 ------------------------------------------------------------------
def crm_summary(c: OdooClient, company_id: int) -> dict:
    co = company(company_id)
    total = c.search_count("crm.lead", [], company_id)
    open_dom = [("active", "=", True), ("probability", "<", 100)]
    stages = c.read_group("crm.lead", open_dom, ["expected_revenue:sum", "stage_id"], ["stage_id"], company_id)
    won = c.read_group("crm.lead", [("probability", "=", 100)], ["expected_revenue:sum", "stage_id"],
                       ["stage_id"], company_id)
    return {"company": co, "currency": co["currency"], "total_leads": total,
            "open": int(sum(g["__count"] for g in stages)),
            "pipeline_value": r2(_group_sum(stages, "expected_revenue")),
            "won": {"count": int(sum(g["__count"] for g in won)), "value": r2(_group_sum(won, "expected_revenue"))},
            "stages": [{"stage": _name(g["stage_id"]) or "None", "count": int(g["__count"]),
                        "value": r2(g.get("expected_revenue"))} for g in stages]}


def sales_summary(c: OdooClient, company_id: int) -> dict:
    co = company(company_id)
    rows = c.read_group("sale.order", [], ["amount_total:sum", "state"], ["state"], company_id)
    return {"company": co, "currency": co["currency"], "order_count": int(sum(g["__count"] for g in rows)),
            "by_state": [{"state": g["state"], "count": int(g["__count"]), "total": r2(g.get("amount_total"))}
                         for g in rows]}


def hr_summary(c: OdooClient, company_id: int) -> dict:
    co = company(company_id)
    rows = c.read_group("hr.employee", [("active", "=", True)], ["department_id"], ["department_id"], company_id)
    pending = c.search_count("hr.leave", [("state", "in", ["confirm", "validate1"])], company_id)
    return {"company": co, "active_headcount": int(sum(g["__count"] for g in rows)),
            "by_department": [{"department": _name(g["department_id"]) or "No department",
                               "count": int(g["__count"])} for g in rows],
            "pending_leaves": pending}


def project_summary(c: OdooClient, company_id: int, today: date) -> dict:
    co = company(company_id)
    total = c.search_count("project.task", [], company_id)
    overdue = c.search_count("project.task", [("date_deadline", "<", today.isoformat()),
                                              ("state", "not in", ["1_done", "1_canceled"])], company_id)
    return {"company": co, "tasks_total": total, "tasks_overdue": overdue}


def loan_summary(c: OdooClient, company_id: int) -> dict:
    co = company(company_id)
    rows = c.read_group("account.loan", [], ["state"], ["state"], company_id)
    return {"company": co, "loan_count": int(sum(g["__count"] for g in rows)),
            "by_state": [{"state": g["state"], "count": int(g["__count"])} for g in rows]}


def _by_state(c, model):
    rows = c.read_group(model, [], ["state"], ["state"])
    return {"count": int(sum(g["__count"] for g in rows)),
            "by_state": [{"state": g["state"], "count": int(g["__count"])} for g in rows]}


def mailings(c: OdooClient, company_id: int) -> dict:
    return {"company": company(company_id), "company_scoped": False, **_by_state(c, "mailing.mailing")}


def social(c: OdooClient, company_id: int) -> dict:
    return {"company": company(company_id), "company_scoped": False, **_by_state(c, "social.post")}
