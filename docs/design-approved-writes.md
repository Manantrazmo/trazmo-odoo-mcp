# Design note: approved writes for the Odoo MCP

Status: proposal, nothing built. The current server stays read-only and is unaffected.

## Goal
Let Claude *propose* a small set of Odoo changes, and apply one only after a human approves it outside the chat.
Reads keep running automatically.

## Non-goals
Posting journal entries, payments, reconciliation, deleting anything, editing users, partners' bank details, or anything on the HR group. Bulk changes. Any write by the read server.

## Principles
1. **The read server never gains a write path.** Writes live in a separate process with its own credential.
2. **The model cannot approve its own proposal.** No approve tool exists. Approval happens in a terminal (later: a small web page) run by a human.
3. **The human approves a server-computed diff, not the model's description.**
4. **Least privilege twice:** a dedicated Odoo user whose access rights cover only the allowed models, plus a server allowlist of models, fields and operations.

## Flow
1. `propose_*` tool (write scope required): validates a strict schema, re-reads current records, computes before/after, stores a **pending change** (`change_id`, payload hash, record `write_date`, login, company, `idempotency_key`, expiry 15 min). Returns `change_id` and the diff. Writes nothing to Odoo.
2. Human runs `approve list`, `approve show <id>` (full diff), then `approve apply <id>` (types the id's last 4 characters) or `reject <id>`.
3. Apply re-reads the record and refuses if `write_date` changed since the proposal, then performs exactly the stored payload with the writer credential, then records the outcome.
4. A repeated `idempotency_key` returns the original result; it never applies twice.

## v1 scope (low money risk, CRM only)
`propose_lead_note` (post a note), `propose_lead_stage_change`, `propose_lead_assign`. Staging first.
v2 (needs its own review): `propose_draft_vendor_bill`, create in **draft only**, never posted. Odoo access rights cannot restrict by state, so the server forces `state=draft` and exposes no post method.

## Controls
- Separate `write` scope per login in `MCP_PRINCIPALS`; default none; wildcard never grants it.
- Limits: 20 proposals/hour/login, 1 record per proposal, text <= 2,000 chars, 15-minute expiry, company scoping on every query.
- Free text from leads is untrusted input: diffs show it verbatim and flagged, and the human reads it before applying.
- Errors are structured `{code, message, trace_id}`; no payloads or keys in logs.
- Audit line per proposal and per decision: change_id, payload hash, login, approver, outcome. Pending store is local SQLite outside the repo (cloud later).
- Fail closed: if the pending store, approver channel, or credential check fails, nothing applies.

## Failure modes
| Risk | Control |
|---|---|
| Model tricked by hostile lead text | Cannot approve; human reviews server diff |
| Stale approval | `write_date` check, 15-minute expiry |
| Double apply | Idempotency key, change marked applied atomically |
| Credential too powerful | Writer Odoo user limited to v1 models; startup probe lists its write access |
| Wrong company | Company scope in proposal and apply |

## Tests (before any merge)
Happy path, duplicate key, stale record, expired change, wrong company, rejected change never applies, model has no approve path, applied payload equals stored payload. Integration runs against a staging Odoo database, never production, with no mocking.

## Open decisions
1. v1 scope: CRM only, or include draft vendor bills.
2. Approval channel: terminal first, or a web page from day one.
3. Who may approve (only you, or named logins), and where the pending store lives.
