#!/usr/bin/env bash
# Deploys trazmo-odoo-mcp to Cloud Run. Idempotent. Never prints secret values.
#
# One-time prerequisite (interactive, so the key never touches shell history or this repo):
#   Create a dedicated READ-ONLY Odoo user + API key, then run:
#     read -rs ODOO_KEY && printf %s "$ODOO_KEY" | gcloud secrets create ODOO_API_KEY --data-file=- ; unset ODOO_KEY
#   (or, if the secret exists, `gcloud secrets versions add ODOO_API_KEY --data-file=-`)
#
# Usage: PROJECT_ID=my-proj ODOO_LOGIN=mcp-readonly@trazmo.com ./deploy.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-trazmo-odoo-mcp}"
SA_NAME="${SA_NAME:-trazmo-odoo-mcp-sa}"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ODOO_URL="${ODOO_URL:-https://trazmo.odoo.com}"
ODOO_DB="${ODOO_DB:-trazmo}"
: "${ODOO_LOGIN:?Set ODOO_LOGIN to the dedicated read-only Odoo user}"

echo "Project=$PROJECT_ID Region=$REGION Service=$SERVICE"

# 1. APIs
gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project "$PROJECT_ID"

# 2. Dedicated service account (only Secret Manager accessor, granted per-secret below)
gcloud iam service-accounts describe "$SA" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name "Trazmo Odoo MCP" --project "$PROJECT_ID"

# 3. Logins: MCP_PRINCIPALS holds one entry per login (name, SHA-256 of its token, allowed tools, allowed companies).
#    Tokens are never stored, only hashes. Bootstrap a minimal smoke-test login if the secret is absent;
#    its token is held in a shell variable (never printed) just to run the smoke test below.
#    Add real logins afterwards with principals_cli (see README), e.g.
#      PYTHONPATH=src python3 -m trazmo_odoo_mcp.principals_cli add director-tamkeen --tools finance --companies 3
SMOKE_TOKEN=""
if ! gcloud secrets describe MCP_PRINCIPALS --project "$PROJECT_ID" >/dev/null 2>&1; then
  SMOKE_TOKEN="$(PYTHONPATH=src python3 -m trazmo_odoo_mcp.principals_cli add smoke-test --tools ops --companies 1 --project "$PROJECT_ID" 2>/dev/null)"
fi
gcloud secrets describe ODOO_API_KEY --project "$PROJECT_ID" >/dev/null 2>&1 || {
  echo "ERROR: secret ODOO_API_KEY missing. See the prerequisite at the top of this script." >&2; exit 1; }

# 4. Least privilege: accessor on just these two secrets
for S in MCP_PRINCIPALS ODOO_API_KEY; do
  gcloud secrets add-iam-policy-binding "$S" --project "$PROJECT_ID" \
    --member "serviceAccount:${SA}" --role roles/secretmanager.secretAccessor >/dev/null
done

# 5. Build from source and deploy
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" --source . \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 \
  --memory 512Mi --cpu 1 --timeout 60 --concurrency 20 \
  --set-env-vars "ODOO_URL=${ODOO_URL},ODOO_DB=${ODOO_DB},ODOO_LOGIN=${ODOO_LOGIN}" \
  --set-secrets "ODOO_API_KEY=ODOO_API_KEY:latest,MCP_PRINCIPALS=MCP_PRINCIPALS:latest"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed: ${URL}/mcp"

# 6. Smoke tests. Needs a token: the bootstrap smoke-test login, or export SMOKE_TOKEN=<any valid token> and re-run.
BODY='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
HDR=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
# Token goes to curl via an fd-backed config, never as an argv entry visible in `ps`.
TOKEN="$SMOKE_TOKEN"
authed() { curl -s "$@" --config <(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN"); }
echo "healthz:        $(curl -s "${URL}/healthz")"
echo "no token:       HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY")  (expect 401)"
echo "wrong token:    HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -H 'Authorization: Bearer wrong' -d "$BODY")  (expect 401)"
if [ -n "$TOKEN" ]; then
  echo "correct token:  HTTP $(authed -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY")  (expect 200)"
  echo "tools:          $(authed -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["result"]["tools"]),"tools visible to smoke-test login (expect 5)")')"
  echo "write attempt:  $(authed -X POST "${URL}/mcp" "${HDR[@]}" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"write","arguments":{}}}' | python3 -c 'import sys,json;r=json.load(sys.stdin);print("REJECTED" if ("error" in r or r["result"].get("isError")) else "!! ACCEPTED")')"
else
  echo "(MCP_PRINCIPALS already existed: skipped authenticated smoke tests. Re-run with SMOKE_TOKEN=<a valid token>.)"
fi
unset TOKEN SMOKE_TOKEN
echo "Remove the smoke-test login when done: PYTHONPATH=src python3 -m trazmo_odoo_mcp.principals_cli revoke smoke-test --project \"$PROJECT_ID\""
