"""Per-login access configuration.

MCP_PRINCIPALS (Secret Manager) is a JSON list; each entry is one login/token:
  {"name": "director-tamkeen", "token_sha256": "<hex>", "tools": ["finance"], "companies": [3]}
`tools`: "*" | list of tool names and/or group names. `companies`: "*" | list of company ids.
Only the SHA-256 of a token is stored, so the secret itself never contains a usable token.
"""
import hashlib
import hmac
import json
import re
from contextvars import ContextVar
from dataclasses import dataclass

from .config import COMPANIES

TOOL_GROUPS = {
    "finance": ["get_invoice_summary", "get_cash_positions", "get_cash_flow_by_month", "get_ytd_mtd",
                "get_expense_breakdown"],
    "crm": ["get_crm_summary", "get_sales_summary"],
    "leads": ["get_lead_funnel", "get_leads_by_source", "get_lead_response_time"],
    "hr": ["get_hr_summary"],
    "ops": ["get_project_summary", "get_loan_summary", "get_mailings", "get_social"],
}
ALL_TOOLS = frozenset(t for g in TOOL_GROUPS.values() for t in g) | {"list_companies"}
# Odoo models with no company field: results are group-wide, so only all-company logins may call these.
GROUP_WIDE_TOOLS = frozenset({"get_mailings", "get_social"})
ALWAYS_ALLOWED = frozenset({"list_companies"})  # filtered to the principal's companies
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Principal:
    name: str
    token_sha256: str
    tools: frozenset | None       # None => all tools
    companies: frozenset | None   # None => all companies

    def can_tool(self, tool: str) -> bool:
        if tool in GROUP_WIDE_TOOLS and self.companies is not None:
            return False  # would leak other companies' data to a company-restricted login
        return tool in ALWAYS_ALLOWED or self.tools is None or tool in self.tools

    def can_company(self, company_id) -> bool:
        return self.companies is None or company_id in self.companies


current_principal: ContextVar[Principal | None] = ContextVar("principal", default=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_principals(text: str) -> tuple[Principal, ...]:
    try:
        raw = json.loads(text)
    except ValueError:
        raise ValueError("MCP_PRINCIPALS is not valid JSON") from None
    if not isinstance(raw, list) or not raw:
        raise ValueError("MCP_PRINCIPALS must be a non-empty JSON list")
    out, names = [], set()
    for e in raw:
        name, digest = e.get("name"), e.get("token_sha256")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Principal names must be unique non-empty strings")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise ValueError(f"Principal {name}: token_sha256 must be 64 hex chars")
        names.add(name)
        out.append(Principal(name, digest, _tools(name, e.get("tools")), _companies(name, e.get("companies"))))
    return tuple(out)


def _tools(name, spec):
    if spec == "*":
        return None
    if not isinstance(spec, list) or not spec:
        raise ValueError(f"Principal {name}: tools must be '*' or a non-empty list")
    resolved = set()
    for item in spec:
        if item in TOOL_GROUPS:
            resolved.update(TOOL_GROUPS[item])
        elif item in ALL_TOOLS:
            resolved.add(item)
        else:
            raise ValueError(f"Principal {name}: unknown tool or group '{item}'")
    return frozenset(resolved)


def _companies(name, spec):
    if spec == "*":
        return None
    if not isinstance(spec, list) or not spec or not all(isinstance(c, int) and c in COMPANIES for c in spec):
        raise ValueError(f"Principal {name}: companies must be '*' or a list of known company ids")
    return frozenset(spec)


def authenticate(principals: tuple[Principal, ...], token: str) -> Principal | None:
    """Constant-time: compares against every principal, no early exit."""
    digest, found = hash_token(token), None
    for p in principals:
        if hmac.compare_digest(digest, p.token_sha256):
            found = p
    return found
