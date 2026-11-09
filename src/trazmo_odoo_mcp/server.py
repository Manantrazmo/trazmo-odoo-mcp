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
from .security import GuardMiddleware, RateLimiter


def _today():
    return datetime.now(ZoneInfo(os.environ.get("REPORT_TZ", "Asia/Karachi"))).date()


def build_mcp(client: OdooClient, today=_today) -> FastMCP:
    mcp = FastMCP(
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
        try:
            result = await asyncio.to_thread(fn, *a)
            if holder[1] and isinstance(result, dict):
                result["truncated"] = True  # a query hit the 500-row cap; figures may be incomplete
            audit(name, company_id, holder[0], True, int((time.monotonic() - t0) * 1000))
            return result
        except (ValueError, OdooError, ReadOnlyViolation) as e:
            audit(name, company_id, holder[0], False, int((time.monotonic() - t0) * 1000), type(e).__name__)
            raise
        except Exception as e:
            audit(name, company_id, holder[0], False, int((time.monotonic() - t0) * 1000), type(e).__name__)
            raise RuntimeError("Internal error") from None

    @mcp.tool()
    async def list_companies() -> dict:
        """List the Trazmo companies (id, name, currency)."""
        return await run("list_companies", None, T.list_companies)

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
    return GuardMiddleware(inner, settings.bearer_token, RateLimiter(settings.rate_limit_per_min))


def create_app_from_env():
    return create_app(Settings.from_env())
