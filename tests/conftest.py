import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trazmo_odoo_mcp.odoo import OdooClient  # noqa: E402

TODAY = date(2026, 11, 9)


class FakeOdoo:
    """Replaces the single XML-RPC choke point. Records every call; routes by (model, method, groupby)."""

    def __init__(self):
        self.calls = []
        self.routes = []  # (predicate(model, method, args, kw) -> bool, response | callable)

    def on(self, pred, response):
        self.routes.append((pred, response))

    def __call__(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        for pred, resp in self.routes:
            if pred(model, method, args, kwargs):
                return resp(model, method, args, kwargs) if callable(resp) else resp
        return 0 if method == "search_count" else []


@pytest.fixture
def fake():
    return FakeOdoo()


@pytest.fixture
def client(fake):
    c = OdooClient("https://odoo.invalid", "db", "login", "SECRET-KEY")
    c._transport_call = fake  # no network, ever
    return c


def gb(model, field):
    return lambda m, meth, a, kw: m == model and meth == "read_group" and kw["groupby"][0].split(":")[0] == field
