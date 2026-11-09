"""Read-only Odoo XML-RPC client.

All access goes through OdooClient.call(), which enforces:
  * method allowlist (search_read, read_group, search_count, fields_get)
  * model allowlist and per-model field allowlist (fields, domains, order, groupby)
  * hard row limit (500)
  * company scoping via domain + allowed_company_ids context
There is deliberately no generic execute_kw passthrough.
"""
import re
import threading
import xmlrpc.client
from dataclasses import dataclass, field

from .audit import add_rows, mark_truncated

MAX_ROWS = 500
READ_METHODS = frozenset({"search_read", "read_group", "search_count", "fields_get"})
OPERATORS = frozenset({
    "=", "!=", ">", ">=", "<", "<=", "in", "not in", "like", "ilike", "not like",
    "not ilike", "=like", "=ilike", "child_of",
})
LOGIC = frozenset({"&", "|", "!"})
AGGS = frozenset({"sum", "avg", "min", "max", "count", "count_distinct"})
GRANULARITY = frozenset({"day", "week", "month", "quarter", "year"})


class ReadOnlyViolation(Exception):
    """Raised for any call that is not an allowlisted read."""


class OdooError(Exception):
    """Sanitised upstream failure (no payloads, no credentials)."""


@dataclass(frozen=True)
class ModelSpec:
    fields: frozenset
    company_field: str | None = None  # None => model is not company-scoped


def _m(fields: str, company_field: str | None = None) -> ModelSpec:
    fs = set(fields.split())
    if company_field:
        fs.add(company_field)
    return ModelSpec(frozenset(fs), company_field)


MODELS: dict[str, ModelSpec] = {
    "account.move": _m(
        "id name move_type state payment_state partner_id invoice_date invoice_date_due date "
        "amount_total amount_residual amount_total_signed amount_residual_signed currency_id",
        "company_id"),
    "account.move.line": _m(
        "id account_id journal_id partner_id date debit credit balance parent_state "
        "account_id.account_type",
        "company_id"),
    "account.journal": _m("id name type default_account_id currency_id active", "company_id"),
    "res.partner": _m("id name is_company parent_id"),
    "res.company": _m("id name currency_id"),
    "crm.lead": _m("id stage_id expected_revenue probability active type create_date date_open day_open "
                   "date_closed source_id medium_id campaign_id lost_reason_id user_id", "company_id"),
    "sale.order": _m("id state amount_total", "company_id"),
    "hr.employee": _m("id department_id active", "company_id"),
    "hr.leave": _m("id state employee_id employee_id.company_id", "employee_id.company_id"),
    "project.task": _m("id date_deadline state stage_id", "company_id"),
    "account.loan": _m("id state", "company_id"),
    "mailing.mailing": _m("id state"),
    "social.post": _m("id state"),
}


def _base(name: str) -> str:
    return name.split(":", 1)[0]


def _check_field(spec: ModelSpec, model: str, name: str) -> None:
    if name not in spec.fields:
        raise ReadOnlyViolation(f"Field not allowed: {model}.{name}")


def _check_domain(spec: ModelSpec, model: str, domain) -> None:
    if not isinstance(domain, (list, tuple)):
        raise ReadOnlyViolation("Invalid domain")
    for leaf in domain:
        if isinstance(leaf, str):
            if leaf not in LOGIC:
                raise ReadOnlyViolation("Invalid domain operator")
            continue
        if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
            raise ReadOnlyViolation("Invalid domain leaf")
        fname, op, _ = leaf
        if not isinstance(fname, str) or op not in OPERATORS:
            raise ReadOnlyViolation("Invalid domain leaf")
        _check_field(spec, model, fname)


def _check_order(spec: ModelSpec, model: str, order: str) -> None:
    for part in order.split(","):
        tokens = part.split()
        if not tokens or len(tokens) > 2 or (len(tokens) == 2 and tokens[1].lower() not in ("asc", "desc")):
            raise ReadOnlyViolation("Invalid order")
        _check_field(spec, model, tokens[0])


_IDENT = re.compile(r"^[a-z_][a-z0-9_.]*$")


def _check_agg_fields(spec: ModelSpec, model: str, fields: list) -> None:
    for f in fields:
        if not isinstance(f, str):
            raise ReadOnlyViolation("Invalid field spec")
        if ":" in f:
            name, agg = f.split(":", 1)
            if agg not in AGGS:
                raise ReadOnlyViolation("Invalid aggregate")
        else:
            name = f
        if not _IDENT.match(name):
            raise ReadOnlyViolation("Invalid field spec")
        _check_field(spec, model, name)


def _check_groupby(spec: ModelSpec, model: str, groupby: list) -> None:
    for g in groupby:
        if not isinstance(g, str):
            raise ReadOnlyViolation("Invalid groupby")
        name, _, gran = g.partition(":")
        if gran and gran not in GRANULARITY:
            raise ReadOnlyViolation("Invalid groupby granularity")
        _check_field(spec, model, name)


_ALLOWED_KW = {
    "search_read": {"fields", "limit", "offset", "order"},
    "read_group": {"fields", "groupby", "limit", "orderby", "lazy", "offset"},
    "search_count": set(),
    "fields_get": {"attributes"},
}
_FIELDS_GET_ATTRS = frozenset({"string", "type", "selection", "relation"})


class OdooClient:
    def __init__(self, url: str, db: str, login: str, api_key: str, timeout: int = 30):
        host = url.split("://", 1)[-1].split("/")[0].split(":")[0]
        if not url.startswith("https://") and host not in ("localhost", "127.0.0.1"):
            raise ValueError("ODOO_URL must be https")
        self._url, self._db, self._login, self._key = url, db, login, api_key
        self._timeout = timeout
        self._uid: int | None = None
        self._lock = threading.Lock()

    # --- transport -------------------------------------------------------
    def _proxy(self, path: str) -> xmlrpc.client.ServerProxy:
        transport = _TimeoutTransport(self._timeout) if self._url.startswith("http:") \
            else _TimeoutSafeTransport(self._timeout)
        return xmlrpc.client.ServerProxy(f"{self._url}/xmlrpc/2/{path}", transport=transport, allow_none=True)

    def _transport_call(self, model: str, method: str, args: list, kwargs: dict):
        """Single choke point to Odoo. Only reached via call() after validation."""
        try:
            with self._lock:
                if self._uid is None:
                    uid = self._proxy("common").authenticate(self._db, self._login, self._key, {})
                    if not uid:
                        raise OdooError("Odoo authentication failed")
                    self._uid = uid
            # New proxy per call: xmlrpc Transport reuses one connection and is not thread-safe.
            return self._proxy("object").execute_kw(self._db, self._uid, self._key, model, method, args, kwargs)
        except OdooError:
            raise
        except Exception as exc:  # never include exc text: it may echo payloads
            raise OdooError(f"Odoo request failed ({type(exc).__name__})") from None

    # --- guarded gate ----------------------------------------------------
    def call(self, model: str, method: str, args=(), kwargs=None, company_id: int | None = None):
        if method not in READ_METHODS:
            raise ReadOnlyViolation(f"Method not allowed: {method}")
        spec = MODELS.get(model)
        if spec is None:
            raise ReadOnlyViolation(f"Model not allowed: {model}")
        kwargs = dict(kwargs or {})
        bad = set(kwargs) - _ALLOWED_KW[method]
        if bad:
            raise ReadOnlyViolation(f"Argument not allowed: {sorted(bad)[0]}")
        args = list(args)

        if method == "fields_get":
            if args:
                raise ReadOnlyViolation("fields_get takes no positional args")
            attrs = kwargs.get("attributes") or ["string", "type"]
            if not set(attrs) <= _FIELDS_GET_ATTRS:
                raise ReadOnlyViolation("Attribute not allowed")
            out = self._transport_call(model, "fields_get", [sorted(spec.fields - {"account_id.account_type", "employee_id.company_id"})],
                                       {"attributes": attrs})
            add_rows(len(out))
            return out

        if len(args) != 1:
            raise ReadOnlyViolation("Exactly one positional arg (domain) required")
        domain = list(args[0])
        _check_domain(spec, model, domain)
        if company_id is not None and spec.company_field:
            domain = [(spec.company_field, "=", company_id)] + domain
        ctx = {"allowed_company_ids": [company_id]} if company_id is not None else {}
        ctx["lang"] = "en_US"
        if model == "res.partner":
            ctx["active_test"] = False  # archived vendors must still resolve (else misclassified as people)
        kw: dict = {"context": ctx}

        if method == "search_count":
            n = self._transport_call(model, method, [domain], kw)
            add_rows(1)
            return n

        if method == "search_read":
            flds = kwargs.get("fields")
            if not flds:
                raise ReadOnlyViolation("fields required")
            _check_agg_fields(spec, model, [f for f in flds if ":" not in f])
            if any(":" in f for f in flds):
                raise ReadOnlyViolation("Aggregates not allowed in search_read")
            kw["fields"] = list(flds)
            kw["limit"] = max(1, min(int(kwargs.get("limit") or MAX_ROWS), MAX_ROWS))
            kw["offset"] = int(kwargs.get("offset") or 0)
            if kwargs.get("order"):
                _check_order(spec, model, kwargs["order"])
                kw["order"] = kwargs["order"]
            rows = self._transport_call(model, method, [domain], kw)
            add_rows(len(rows))
            if len(rows) >= kw["limit"] and kw["limit"] == MAX_ROWS:
                mark_truncated()
            return rows

        # read_group
        flds, gb = kwargs.get("fields") or [], kwargs.get("groupby") or []
        if not gb:
            raise ReadOnlyViolation("groupby required")
        _check_agg_fields(spec, model, flds)
        _check_groupby(spec, model, gb)
        kw.update(fields=list(flds), groupby=list(gb), lazy=False,
                  limit=max(1, min(int(kwargs.get("limit") or MAX_ROWS), MAX_ROWS)))
        if kwargs.get("orderby"):
            _check_order(spec, model, kwargs["orderby"])
            kw["orderby"] = kwargs["orderby"]
        rows = self._transport_call(model, method, [domain], kw)
        add_rows(len(rows))
        if len(rows) >= MAX_ROWS:
            mark_truncated()
        return rows

    # --- convenience wrappers (all via call) -------------------------------
    def search_read(self, model, domain, fields, company_id=None, **kw):
        return self.call(model, "search_read", [domain], {"fields": fields, **kw}, company_id)

    def read_group(self, model, domain, fields, groupby, company_id=None, **kw):
        return self.call(model, "read_group", [domain], {"fields": fields, "groupby": groupby, **kw}, company_id)

    def search_count(self, model, domain, company_id=None):
        return self.call(model, "search_count", [domain], {}, company_id)

    def fields_get(self, model, attributes=None):
        return self.call(model, "fields_get", [], {"attributes": attributes} if attributes else {})


class _TimeoutMixin:
    def __init__(self, timeout):
        super().__init__()
        self._t = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._t
        return conn


class _TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    pass


class _TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    pass
