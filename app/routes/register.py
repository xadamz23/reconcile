import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.money import parse_cents
from app.routes.deps import get_conn, is_htmx, render

router = APIRouter()


def register_context(conn: sqlite3.Connection, kind: str, q: str, amount: str, show_reconciled: bool) -> dict:
    kind = kind if kind in repo.KINDS else "all"
    error = None
    try:
        rows = repo.list_txns(conn, kind=kind, q=q, amount=amount, show_reconciled=show_reconciled)
    except ValueError:
        rows, error = [], f"“{amount}” isn't an amount. Try 9.99 or a range like 10-50."
    balance, verified = repo.totals(conn)
    return {
        "rows": rows,
        "balance": balance,
        "verified": verified,
        "account_name": repo.get_setting(conn, "account_name", "Checking"),
        "filters": {"kind": kind, "q": q, "amount": amount, "show_reconciled": show_reconciled},
        "filtering": bool(q.strip() or amount.strip() or kind != "all"),
        "filter_error": error,
    }


@router.get("/")
def register(
    request: Request,
    kind: str = "all",
    q: str = "",
    amount: str = "",
    show_reconciled: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    ctx = register_context(conn, kind, q, amount, show_reconciled)
    if is_htmx(request) and request.headers.get("HX-Target") == "ledger":
        return render(request, "_ledger.html", {**ctx, "oob_totals": True})
    return render(request, "register.html", ctx)


@router.get("/setup")
def setup_form(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if repo.is_setup(conn):
        return RedirectResponse("/", status_code=303)
    return render(request, "setup.html", {"form": {"account_name": "", "opening": "", "date": date.today().isoformat()}})


@router.post("/setup")
def setup_submit(
    request: Request,
    account_name: str = Form(""),
    opening: str = Form(""),
    on_date: str = Form(..., alias="date"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if repo.is_setup(conn):
        return RedirectResponse("/", status_code=303)
    errors = {}
    try:
        cents = parse_cents(opening)
    except ValueError:
        errors["opening"] = "Enter your current balance, e.g. 1234.56"
    try:
        date.fromisoformat(on_date)
    except ValueError:
        errors["date"] = "Pick a date"
    if errors:
        form = {"account_name": account_name, "opening": opening, "date": on_date}
        return render(request, "setup.html", {"form": form, "errors": errors})
    repo.setup(conn, account_name.strip() or "Checking", cents, on_date)
    return RedirectResponse("/", status_code=303)
