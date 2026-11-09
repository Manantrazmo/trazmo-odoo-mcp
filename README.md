# trazmo-odoo-mcp

Read-only MCP server (Python 3.12, official MCP SDK `FastMCP`, streamable HTTP, stateless, `/mcp`) over Trazmo's Odoo, for Cloud Run.
Replaces an Odoo API key pasted into a scheduled-task prompt: the prompt holds no credentials.

## Security model
- **Auth:** `Authorization: Bearer <token>` on `/mcp` (header only; a token in the URL is ignored). Constant-time compare (SHA-256 digests + `hmac.compare_digest`). Missing/wrong → `401`, empty body. `/healthz` returns `ok` only.
- **Read-only by construction:** no `execute_kw` passthrough. `OdooClient.call()` accepts only `search_read`, `read_group`, `search_count`, `fields_get`, against a model + field allowlist (`src/trazmo_odoo_mcp/odoo.py`). Domains, `fields`, `groupby`, `order` are all validated; caller-supplied `context` is rejected; hard limit 500 rows; every query is scoped to `company_id`.
- **Privacy:** individuals (non-company partners) are never returned next to an amount. Expense vendors roll up to `Staff & individual reimbursements (N people, aggregated)`; invoice top items show `Individual (name withheld)`.
- **Logging:** one JSON audit line per call (tool, company_id, row_count, ok, duration, timestamp, error class). No tokens, keys, arguments or results. Uvicorn access log is off.
- **Ops:** Cloud Run min 0 / max 2, dedicated service account with `secretAccessor` on the two secrets only, fixed-window rate limit per instance (`RATE_LIMIT_PER_MIN`, default 300 → 429; each tool call costs ~3 HTTP requests in stateless mode).
- Real security boundary for Odoo data is the **Odoo user's own access rights**. Make it a dedicated read-only user (no write ACLs on anything) and grant it all three companies.

## Tools (all take `company_id`: 1 Trazmo Pvt Ltd PKR, 2 Trazmo Holdings USD, 3 Tamkeen Financial PKR)
`list_companies`, `get_invoice_summary`, `get_cash_positions`, `get_cash_flow_by_month(months=6)`, `get_ytd_mtd`, `get_expense_breakdown(months=6)`, `get_crm_summary`, `get_sales_summary`, `get_hr_summary`, `get_project_summary`, `get_loan_summary`, `get_mailings`, `get_social`.
`mailing.mailing` and `social.post` have no company field in Odoo, so those two are group level (`company_scoped: false`). Dates use `REPORT_TZ` (default Asia/Karachi). Results include `"truncated": true` if any query hit the 500-row cap.

## Develop & test
```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
Run locally and poke it with the MCP inspector (dummy Odoo is fine for `tools/list`):
```bash
export ODOO_URL=https://trazmo.odoo.com ODOO_DB=trazmo ODOO_LOGIN=... ODOO_API_KEY=... MCP_BEARER_TOKEN="$(openssl rand -base64 48)" PORT=18765
PYTHONPATH=src .venv/bin/python -m trazmo_odoo_mcp &
npx @modelcontextprotocol/inspector --cli http://localhost:18765/mcp --transport http --method tools/list --header "Authorization: Bearer $MCP_BEARER_TOKEN"
```

## Deploy
1. In Odoo create the dedicated read-only user and an API key for it.
2. Store the key without echoing it, then run the deploy script:
```bash
gcloud config set project <PROJECT_ID>
read -rs ODOO_KEY && printf %s "$ODOO_KEY" | gcloud secrets create ODOO_API_KEY --data-file=- ; unset ODOO_KEY
ODOO_LOGIN=<readonly-user-login> ./deploy.sh
```
`deploy.sh` enables APIs, creates the service account, generates the bearer token with `openssl rand -base64 48` straight into Secret Manager (never printed), grants per-secret access, deploys, then smoke-tests healthz / 401 / 200 / tools count / write rejection.

Read the token once to paste into claude.ai (it prints to your terminal, so do this privately):
```bash
gcloud secrets versions access latest --secret MCP_BEARER_TOKEN | pbcopy
```
Rotate: `openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add MCP_BEARER_TOKEN --data-file=-` then redeploy (or `gcloud run services update trazmo-odoo-mcp --region <r>`) so instances pick up `latest`.

## Add to claude.ai
Settings → Connectors → **Add custom connector** → URL `<cloud-run-url>/mcp` → Request headers: name `Authorization`, value `Bearer <token>`.
