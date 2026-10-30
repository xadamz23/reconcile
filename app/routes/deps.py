import sqlite3
from collections.abc import Iterator
from datetime import date

from fastapi import Request

from app import repo


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    # One connection is shared by every request, and sync routes run on several
    # threads: without the lock, one request's BEGIN/ROLLBACK can land inside
    # another's transaction. A plain Lock, since FastAPI may release it on another thread.
    with request.app.state.db_lock:
        yield request.app.state.conn


def render(request: Request, name: str, context: dict | None = None, **kwargs):
    ctx = {"today": date.today().isoformat(), **(context or {})}
    return request.app.state.templates.TemplateResponse(request, name, ctx, **kwargs)


def masthead_context(conn: sqlite3.Connection) -> dict:
    return {
        "balance": repo.balance(conn),
        "last": repo.last_statement(conn),
        "reconciling": repo.get_draft(conn) is not None,
        "account_name": repo.get_setting(conn, "account_name", "Checking"),
    }


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
