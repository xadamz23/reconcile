import sqlite3
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.money import parse_cents
from app.routes.deps import get_conn, render

router = APIRouter()


def _state(conn, end_date: str, balance: str) -> repo.ReconcileState | None:
    try:
        return repo.reconcile_state(conn, end_date, parse_cents(balance))
    except ValueError:
        return None


@router.get("/reconcile")
def reconcile(
    request: Request,
    end_date: str = "",
    balance: str = "",
    done: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    state = _state(conn, end_date, balance) if end_date and balance else None
    error = "Enter the statement's closing date and ending balance." if (end_date or balance) and not state else None
    return render(request, "reconcile.html", {
        "state": state,
        "form": {"end_date": end_date or date.today().isoformat(), "balance": balance},
        "error": error,
        "done": done,
        "last": repo.last_statement(conn),
        "statements": repo.list_statements(conn),
        "account_name": repo.get_setting(conn, "account_name", "Checking"),
    })


@router.post("/reconcile/{txn_id}/toggle")
def reconcile_toggle(
    request: Request,
    txn_id: int,
    end_date: str = Form(...),
    balance: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if repo.get_txn(conn, txn_id) is None:
        raise HTTPException(404)
    repo.toggle_verified(conn, txn_id)
    state = _state(conn, end_date, balance)
    if state is None:
        raise HTTPException(400)
    return render(request, "_reconcile_toggled.html", {
        "row": repo.get_txn(conn, txn_id),
        "state": state,
        "form": {"end_date": end_date, "balance": balance},
    })


@router.post("/reconcile/finish")
def reconcile_finish(
    end_date: str = Form(...),
    balance: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        sid = repo.finish_statement(conn, end_date, parse_cents(balance))
    except ValueError:
        return RedirectResponse("/reconcile?" + urlencode({"end_date": end_date, "balance": balance}), status_code=303)
    return RedirectResponse(f"/reconcile?done={sid}", status_code=303)
