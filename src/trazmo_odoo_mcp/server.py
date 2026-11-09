import asyncio
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import tools as T
from .audit import audit, start_row_counter
from .config import Settings
from .odoo import OdooClient, OdooError, ReadOnlyViolation
from .principals import current_principal
from .security import GuardMiddleware


def _today():
    return datetime.now(ZoneInfo(os.environ.get("REPORT_TZ", "Asia/Karachi"))).date()


class ScopedMCP(FastMCP):
    """tools/list shows each login only the tools it may call."""

    async def list_tools(self):
        p = current_principal.get()
        return [t for t in await super().list_tools() if p is not None and p.can_tool(t.name)]


def build_mcp(client: OdooClient, today=_today) -> FastMCP:
    mcp = ScopedMCP(
        "trazmo-odoo",
        instructions="Read-only aggregated Odoo reporting for Trazmo. Every tool takes company_id "
                     "(1=Trazmo Pvt Ltd PKR, 2=Trazmo Holdings USD, 3=Tamkeen Financial PKR).",
        stateless_http=True, json_response=True, streamable_http_path="/mcp",
        host="0.0.0.0",
        # Host header is the Cloud Run hostname; the bearer token is the gate.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def run(name: str, company_id, fn, *a):
        holder = start_row_counter()
        t0 = time.monotonic()
        p = current_principal.get()
        who = p.name if p else None
        try:
            if p is None or not p.can_tool(name):  # fail closed; same message for unknown/forbidden
                raise ValueError("Not permitted")
            if company_id is not None and not p.can_company(company_id):
                raise ValueError("Not permitted")
            result = await asyncio.to_thread(fn, *a)
            if holder[1] and isinstance(result, dict):
                result["truncated"] = True  # a query hit the 500-row cap; figures may be incomplete
            audit(name, company_id, holder[0], True, int((time.monotonic() - t0) * 1000), principal=who)
            return result
        except (ValueError, OdooError, ReadOnlyViolation) as e:
            audit(name, company_id, holder[0], False, int((time.monotonic() - t0) * 1000), type(e).__name__, who)
            raise
        except Exception as e:
            audit(name, company_id, holder[0], False, int((time.monotonic() - t0) * 1000), type(e).__name__, who)
            raise RuntimeError("Internal error") from None

    @mcp.tool()
    async def list_companies() -> dict:
        """List the Trazmo companies (id, name, currency)."""
        res = await run("list_companies", None, T.list_companies)
        p = current_principal.get()
        return {"companies": [c for c in res["companies"] if p and p.can_company(c["id"])]}

    @mcp.tool()
    async def get_invoice_summary(company_id: int) -> dict:
        """Posted customer/vendor invoices: counts, totals, AR/AP outstanding, aging buckets, top 5 open items."""
        return await run("get_invoice_summary", company_id, T.invoice_summary, client, company_id, today())

    @mcp.tool()
    async def get_cash_positions(company_id: int) -> dict:
        """Bank and cash journal balances (posted lines on each journal's default account)."""
        return await run("get_cash_positions", company_id, T.cash_positions, client, company_id, today())

    @mcp.tool()
    async def get_cash_flow_by_month(company_id: int, months: int = 6) -> dict:
        """Net monthly movement on cash accounts, posted only. Current month is marked partial."""
        return await run("get_cash_flow_by_month", company_id, T.cash_flow_by_month, client, company_id, today(), months)

    @mcp.tool()
    async def get_ytd_mtd(company_id: int) -> dict:
        """Income and expense YTD/MTD (P&L basis) with prior-year YTD and prior-month comparison, top 8 accounts."""
        return await run("get_ytd_mtd", company_id, T.ytd_mtd, client, company_id, today())

    @mcp.tool()
    async def get_expense_breakdown(company_id: int, months: int = 6) -> dict:
        """Expenses by account (top 8 + Other) and by vendor; individuals are aggregated into one line."""
        return await run("get_expense_breakdown", company_id, T.expense_breakdown, client, company_id, today(), months)

    @mcp.tool()
    async def get_crm_summary(company_id: int) -> dict:
        """CRM totals, open count, pipeline value, won, and stage breakdown."""
        return await run("get_crm_summary", company_id, T.crm_summary, client, company_id)

    @mcp.tool()
    async def get_lead_funnel(company_id: int, days: int = 30) -> dict:
        """Lead funnel for leads created in the last N days (1-365): created, qualified, open, won, lost, stages, lost reasons. Aggregates only; no contact details."""
        return await run("get_lead_funnel", company_id, T.lead_funnel, client, company_id, today(), days)

    @mcp.tool()
    async def get_leads_by_source(company_id: int, days: int = 30) -> dict:
        """Lead volume and win rate by source, medium and campaign (top 10 + Other) for the last N days."""
        return await run("get_leads_by_source", company_id, T.leads_by_source, client, company_id, today(), days)

    @mcp.tool()
    async def get_lead_response_time(company_id: int, days: int = 30) -> dict:
        """Time from lead creation to assignment (response-time proxy) and open leads nobody owns."""
        return await run("get_lead_response_time", company_id, T.lead_response_time, client, company_id, today(), days)

    @mcp.tool()
    async def search_transactions(company_id: int, date_from: str | None = None, date_to: str | None = None,
                                  account_type: str | None = None, journal_type: str | None = None,
                                  account_search: str | None = None, counterparty_search: str | None = None,
                                  min_amount: float | None = None, max_amount: float | None = None,
                                  limit: int = 50, offset: int = 0) -> dict:
        """Posted journal items (read-only), newest first, max 100 per call (use offset). Dates YYYY-MM-DD, span <= 366 days (default last 30). Filters: account_type (e.g. expense, asset_cash), journal_type (sale|purchase|bank|cash|general), account_search (code prefix or name), counterparty_search (companies only), min/max amount. Individuals' names are withheld; memos and bank details are never returned."""
        return await run("search_transactions", company_id, T.search_transactions, client, company_id, today(),
                         date_from, date_to, account_type, journal_type, account_search, counterparty_search,
                         min_amount, max_amount, limit, offset)

    @mcp.tool()
    async def get_transaction_totals(company_id: int, group_by: str = "account", date_from: str | None = None,
                                     date_to: str | None = None, account_type: str | None = None,
                                     journal_type: str | None = None, account_search: str | None = None) -> dict:
        """Posted transaction totals (debit, credit, net) grouped by account, partner or journal for a date range (<= 366 days). Individuals are aggregated, never named."""
        return await run("get_transaction_totals", company_id, T.transaction_totals, client, company_id, today(),
                         group_by, date_from, date_to, account_type, journal_type, account_search)

    @mcp.tool()
    async def get_sales_summary(company_id: int) -> dict:
        """Sale order count (with state breakdown)."""
        return await run("get_sales_summary", company_id, T.sales_summary, client, company_id)

    @mcp.tool()
    async def get_hr_summary(company_id: int) -> dict:
        """Active headcount by department and pending leave requests."""
        return await run("get_hr_summary", company_id, T.hr_summary, client, company_id)

    @mcp.tool()
    async def get_project_summary(company_id: int) -> dict:
        """Project tasks total and overdue."""
        return await run("get_project_summary", company_id, T.project_summary, client, company_id, today())

    @mcp.tool()
    async def get_loan_summary(company_id: int) -> dict:
        """Loan (account.loan) count with state breakdown."""
        return await run("get_loan_summary", company_id, T.loan_summary, client, company_id)

    @mcp.tool()
    async def get_mailings(company_id: int) -> dict:
        """Mass mailings, group level (not company-scoped in Odoo)."""
        return await run("get_mailings", company_id, T.mailings, client, company_id)

    @mcp.tool()
    async def get_social(company_id: int) -> dict:
        """Social posts, group level (not company-scoped in Odoo)."""
        return await run("get_social", company_id, T.social, client, company_id)

    return mcp


def create_app(settings: Settings, client: OdooClient | None = None, today=_today):
    client = client or OdooClient(settings.odoo_url, settings.odoo_db, settings.odoo_login, settings.odoo_api_key)
    inner = build_mcp(client, today).streamable_http_app()
    return GuardMiddleware(inner, settings.principals, settings.rate_limit_per_min)


def create_app_from_env():
    return create_app(Settings.from_env())
