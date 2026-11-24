import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from fastapi import FastAPI, Request

from app import repo


@contextmanager
def locked_conn(app: FastAPI) -> Iterator[sqlite3.Connection]:
    # One connection is shared by every request, and sync routes run on several
    # threads: without the lock, one request's BEGIN/ROLLBACK can land inside
    # another's transaction. A plain Lock, since FastAPI may release it on another thread.
    with app.state.db_lock:
        yield app.state.conn


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """The connection, locked for the whole request. Routes take it through Depends; the
    one exception is reading a statement, which mustn't hold the lock while Claude reads
    and uses locked_conn around its own database work instead."""
    with locked_conn(request.app) as conn:
        yield conn


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
