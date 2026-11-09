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

# 3. Bearer token: generated and piped straight into Secret Manager, never printed.
#    Only created if absent (rotate with: openssl rand -base64 48 | gcloud secrets versions add MCP_BEARER_TOKEN --data-file=-)
if ! gcloud secrets describe MCP_BEARER_TOKEN --project "$PROJECT_ID" >/dev/null 2>&1; then
  openssl rand -base64 48 | tr -d '\n' | \
    gcloud secrets create MCP_BEARER_TOKEN --data-file=- --replication-policy=automatic --project "$PROJECT_ID"
fi
gcloud secrets describe ODOO_API_KEY --project "$PROJECT_ID" >/dev/null 2>&1 || {
  echo "ERROR: secret ODOO_API_KEY missing. See the prerequisite at the top of this script." >&2; exit 1; }

# 4. Least privilege: accessor on just these two secrets
for S in MCP_BEARER_TOKEN ODOO_API_KEY; do
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
  --set-secrets "ODOO_API_KEY=ODOO_API_KEY:latest,MCP_BEARER_TOKEN=MCP_BEARER_TOKEN:latest"

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed: ${URL}/mcp"

# 6. Smoke tests (token read from Secret Manager into a variable; never echoed)
TOKEN="$(gcloud secrets versions access latest --secret MCP_BEARER_TOKEN --project "$PROJECT_ID")"
BODY='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
HDR=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
# Token goes to curl via an fd-backed config, never as an argv entry visible in `ps`.
authed() { curl -s "$@" --config <(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN"); }
echo "healthz:        $(curl -s "${URL}/healthz")"
echo "no token:       HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY")  (expect 401)"
echo "wrong token:    HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -H 'Authorization: Bearer wrong' -d "$BODY")  (expect 401)"
echo "correct token:  HTTP $(authed -o /dev/null -w '%{http_code}' -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY")  (expect 200)"
echo "tools:          $(authed -X POST "${URL}/mcp" "${HDR[@]}" -d "$BODY" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["result"]["tools"]),"tools")')"
echo "write attempt:  $(authed -X POST "${URL}/mcp" "${HDR[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"write","arguments":{}}}' | python3 -c 'import sys,json;r=json.load(sys.stdin);print("REJECTED" if ("error" in r or r["result"].get("isError")) else "!! ACCEPTED")')"
unset TOKEN
