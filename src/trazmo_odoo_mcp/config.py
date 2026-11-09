import os
from dataclasses import dataclass

COMPANIES = {
    1: {"id": 1, "name": "Trazmo Pvt Ltd", "currency": "PKR"},
    2: {"id": 2, "name": "Trazmo Holdings", "currency": "USD"},
    3: {"id": 3, "name": "Tamkeen Financial", "currency": "PKR"},
}


def _principals(text):
    from .principals import parse_principals  # late import: principals imports COMPANIES from here
    return parse_principals(text)


@dataclass(frozen=True)
class Settings:
    odoo_url: str
    odoo_db: str
    odoo_login: str
    odoo_api_key: str
    principals: tuple
    rate_limit_per_min: int = 300

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [
            k for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY", "MCP_PRINCIPALS")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
        return cls(
            odoo_url=os.environ["ODOO_URL"].rstrip("/"),
            odoo_db=os.environ["ODOO_DB"],
            odoo_login=os.environ["ODOO_LOGIN"],
            odoo_api_key=os.environ["ODOO_API_KEY"],
            principals=_principals(os.environ["MCP_PRINCIPALS"]),
            rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "300")),
        )
