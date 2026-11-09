"""Structured audit logging (Cloud Logging JSON on stdout). Never logs payloads."""
import json
import sys
import time
from contextvars import ContextVar

# Mutable holder so rows counted in worker threads are visible to the caller.
_rows: ContextVar[list | None] = ContextVar("rows", default=None)


def start_row_counter() -> list:
    holder = [0, False]  # [rows, truncated]
    _rows.set(holder)
    return holder


def add_rows(n: int) -> None:
    holder = _rows.get()
    if holder is not None:
        holder[0] += n


def mark_truncated() -> None:
    holder = _rows.get()
    if holder is not None:
        holder[1] = True


def audit(tool: str, company_id, rows: int, ok: bool, ms: int, error: str | None = None) -> None:
    entry = {
        "severity": "INFO" if ok else "ERROR",
        "message": "tool_call",
        "event": "audit",
        "tool": tool,
        "company_id": company_id,
        "row_count": rows,
        "ok": ok,
        "duration_ms": ms,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        entry["error_type"] = error  # exception class name only
    sys.stdout.write(json.dumps(entry) + "\n")
    sys.stdout.flush()
