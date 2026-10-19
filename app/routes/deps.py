import sqlite3
from datetime import date

from fastapi import Request


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def render(request: Request, name: str, context: dict | None = None, **kwargs):
    ctx = {"today": date.today().isoformat(), **(context or {})}
    return request.app.state.templates.TemplateResponse(request, name, ctx, **kwargs)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
