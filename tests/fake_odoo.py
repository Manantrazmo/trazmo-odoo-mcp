"""Tiny fake Odoo XML-RPC server (authenticate + execute_kw) returning realistic read_group/search_read shapes.

Exercises the real transport path (xmlrpc, auth, serialisation, threads). Not an Odoo emulator:
figures are canned. Run standalone:  python tests/fake_odoo.py 19000
"""
import sys
import threading
from socketserver import ThreadingMixIn
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

KEY = "ro-key"
READS = {"search_read", "read_group", "search_count", "fields_get"}
PARTNERS = [
    {"id": 1, "name": "Acme Supplies Ltd", "is_company": True, "parent_id": False},
    {"id": 2, "name": "Ali Khan", "is_company": False, "parent_id": False},
    {"id": 3, "name": "Sara Ahmed", "is_company": False, "parent_id": False},
    {"id": 4, "name": "Omar Raza", "is_company": False, "parent_id": False},
    {"id": 5, "name": "Zed Contact", "is_company": False, "parent_id": [1, "Acme Supplies Ltd"]},
]


class Fake:
    def __init__(self):
        self.seen = []  # (model, method, context)
        self.lock = threading.Lock()

    def authenticate(self, db, login, key, ctx):
        return 7 if (db == "trazmo" and key == KEY) else False

    def execute_kw(self, db, uid, key, model, method, args, kwargs):
        with self.lock:
            self.seen.append((model, method, kwargs.get("context", {})))
        if key != KEY or uid != 7:
            raise PermissionError("denied")
        if method not in READS:
            raise PermissionError("write attempted")  # the server must never get here
        dom = args[0] if args else []
        if method == "search_count":
            return 7
        if method == "fields_get":
            return {}
        if method == "search_read":
            if model == "res.partner":
                ids = [leaf[2] for leaf in dom if isinstance(leaf, list) and leaf[0] == "id"][0]
                return [p for p in PARTNERS if p["id"] in ids]
            if model == "account.journal":
                return [{"id": 1, "name": "Bank", "type": "bank", "default_account_id": [10, "101000 Bank"], "currency_id": False},
                        {"id": 2, "name": "Cash", "type": "cash", "default_account_id": [11, "101500 Cash"], "currency_id": False}]
            if model == "account.move.line":
                return [{"id": 1, "date": "2026-11-01", "move_name": "BNK1/2026/0001", "journal_id": [1, "Bank"],
                         "account_id": [30, "600000 Rent"], "partner_id": [1, "Acme Supplies Ltd"], "debit": 700.0, "credit": 0.0},
                        {"id": 2, "date": "2026-10-30", "move_name": "BNK1/2026/0002", "journal_id": [1, "Bank"],
                         "account_id": [30, "600000 Rent"], "partner_id": [2, "Ali Khan"], "debit": 55.0, "credit": 0.0}]
            if model == "account.move":
                return [{"id": 1, "name": "INV/2026/0001", "partner_id": [1, "Acme Supplies Ltd"],
                         "invoice_date_due": "2026-10-01", "amount_residual_signed": 1200.5},
                        {"id": 2, "name": "INV/2026/0002", "partner_id": [2, "Ali Khan"],
                         "invoice_date_due": False, "amount_residual_signed": 300.0}]
            return []
        gb = kwargs["groupby"][0].split(":")[0]
        g = {
            "move_type": [{"move_type": "out_invoice", "__count": 12, "amount_total_signed": 5000.0, "amount_residual_signed": 1500.5},
                          {"move_type": "in_invoice", "__count": 6, "amount_total_signed": -2400.0, "amount_residual_signed": -800.0}],
            "account_id": [{"account_id": [10, "101000 Bank"], "__count": 3, "balance": 9000.0},
                           {"account_id": [11, "101500 Cash"], "__count": 1, "balance": 250.0},
                           {"account_id": [20, "400000 Sales"], "__count": 5, "balance": -3000.0},
                           {"account_id": [30, "600000 Rent"], "__count": 2, "balance": 700.0}],
            "partner_id": [{"partner_id": [1, "Acme Supplies Ltd"], "__count": 2, "balance": 500.0},
                           {"partner_id": [2, "Ali Khan"], "__count": 1, "balance": 120.0},
                           {"partner_id": [3, "Sara Ahmed"], "__count": 1, "balance": 80.0},
                           {"partner_id": [4, "Omar Raza"], "__count": 1, "balance": 60.0},
                           {"partner_id": [5, "Zed Contact"], "__count": 1, "balance": 10.0}],
            "company_id": [{"company_id": [(kwargs["context"].get("allowed_company_ids") or [0])[0], "Co"], "__count": 4,
                            "debit": 400.0, "credit": 150.0, "balance": 250.0, "day_open": 1.5}],
            "source_id": [{"source_id": [1, "Website"], "__count": 9}, {"source_id": [2, "Referral"], "__count": 3}],
            "medium_id": [{"medium_id": [1, "Organic"], "__count": 8}, {"medium_id": False, "__count": 4}],
            "campaign_id": [{"campaign_id": [1, "Q4 Launch"], "__count": 6}],
            "lost_reason_id": [{"lost_reason_id": [1, "Too expensive"], "__count": 2}, {"lost_reason_id": False, "__count": 1}],
            "stage_id": [{"stage_id": [1, "New"], "__count": 4, "expected_revenue": 1000.0}],
            "state": [{"state": "sale", "__count": 3, "amount_total": 90.0}],
            "department_id": [{"department_id": [1, "Engineering"], "__count": 5}, {"department_id": False, "__count": 1}],
        }[gb]
        return g


class _Server(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128  # default 5 resets connections under concurrency


def start(port: int = 0):
    fake = Fake()

    class H(SimpleXMLRPCRequestHandler):
        rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")
        def log_message(self, *a):  # silence
            pass

    srv = _Server(("127.0.0.1", port), H, allow_none=True, logRequests=False)
    srv.register_function(fake.authenticate, "authenticate")
    srv.register_function(fake.execute_kw, "execute_kw")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, fake


if __name__ == "__main__":
    srv, _ = start(int(sys.argv[1]))
    print(f"fake odoo on {srv.server_address}", flush=True)
    threading.Event().wait()
