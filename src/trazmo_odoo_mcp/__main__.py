import os
import sys

import uvicorn


def run_stdio() -> None:
    """Launched by the MCP client as a child process: starts on use, exits when the client closes."""
    from . import audit
    from .config import Settings
    from .odoo import OdooClient
    from .principals import current_principal
    from .server import build_mcp

    audit.stream = sys.stderr  # stdout carries the protocol
    s = Settings.from_env()
    by_name = {p.name: p for p in s.principals}
    wanted = os.environ.get("MCP_STDIO_PRINCIPAL")
    principal = by_name.get(wanted) if wanted else (by_name.get("local") or
                                                    (s.principals[0] if len(s.principals) == 1 else None))
    if principal is None:
        sys.exit("Set MCP_STDIO_PRINCIPAL to one of the configured login names")
    current_principal.set(principal)
    client = OdooClient(s.odoo_url, s.odoo_db, s.odoo_login, s.odoo_api_key)
    build_mcp(client).run(transport="stdio")


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        run_stdio()
    else:
        from .server import create_app_from_env
        uvicorn.run(
            create_app_from_env(),
            host=os.environ.get("HOST", "127.0.0.1"),  # local-only by default; the container sets HOST=0.0.0.0
            port=int(os.environ.get("PORT", "8080")),
            log_level="warning",
            access_log=False,  # never log request lines/headers
        )
