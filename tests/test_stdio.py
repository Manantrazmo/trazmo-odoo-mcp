"""The stdio launch mode: the client starts the server as a child process, talks MCP over stdin/stdout."""
import asyncio
import json
import os
import sys

import fake_odoo
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_http import FULL, P  # noqa: F401
from trazmo_odoo_mcp.principals import hash_token

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


async def _session(env):
    params = StdioServerParameters(command=sys.executable, args=["-m", "trazmo_odoo_mcp", "--stdio"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            ok = await s.call_tool("get_crm_summary", {"company_id": 2})
            denied = await s.call_tool("get_hr_summary", {"company_id": 2})
            return tools, ok, denied


def test_stdio_mode_serves_tools_scoped_to_its_login():
    srv, _ = fake_odoo.start()
    login = {"name": "local", "token_sha256": hash_token("x"), "tools": ["crm", "ops"], "companies": [2]}
    env = {**os.environ, "PYTHONPATH": SRC, "ODOO_URL": f"http://127.0.0.1:{srv.server_address[1]}",
           "ODOO_DB": "trazmo", "ODOO_LOGIN": "ro", "ODOO_API_KEY": fake_odoo.KEY, "MCP_PRINCIPALS": json.dumps([login])}
    try:
        tools, ok, denied = asyncio.run(asyncio.wait_for(_session(env), 60))
    finally:
        srv.shutdown()
    assert "get_crm_summary" in tools and "get_hr_summary" not in tools and "get_invoice_summary" not in tools
    assert not ok.isError and json.loads(ok.content[0].text)["company"]["id"] == 2
    assert denied.isError  # outside this login's tools
