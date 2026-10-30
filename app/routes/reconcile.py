import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.money import parse_cents, plain_amount
from app.routes.deps import get_conn, is_htmx, masthead_context, render

router = APIRouter()


def _state(conn) -> repo.ReconcileState | None:
    draft = repo.get_draft(conn)
    return repo.reconcile_state(conn, *draft) if draft else None


def _page(request: Request, conn, form: dict | None = None, error: str | None = None, done: int | None = None):
    state = _state(conn)
    if form is None and state:
        sign = "-" if state.statement_cents < 0 else ""
        form = {"end_date": state.end_date, "balance": sign + plain_amount(state.statement_cents)}
    elif form is None:
        form = {"end_date": date.today().isoformat(), "balance": ""}
    return render(request, "reconcile.html", {
        "state": state,
        "form": form,
        "error": error,
        "done": done,
        "statements": repo.list_statements(conn),
        **masthead_context(conn),
    })


@router.get("/reconcile")
def reconcile(request: Request, done: int | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    if is_htmx(request) and request.headers.get("HX-Target") == "rec-body":
        return render(request, "_reconcile_body.html", {"state": _state(conn), "oob_totals": True, **masthead_context(conn)})
    return _page(request, conn, done=done)


@router.post("/reconcile/start")
def reconcile_start(
    request: Request,
    end_date: str = Form(""),
    balance: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        repo.save_draft(conn, end_date, parse_cents(balance))
    except ValueError:
        error = "Enter the statement's closing date and ending balance."
        return _page(request, conn, form={"end_date": end_date, "balance": balance}, error=error)
    return RedirectResponse("/reconcile", status_code=303)


@router.post("/reconcile/discard")
def reconcile_discard(conn: sqlite3.Connection = Depends(get_conn)):
    repo.discard_draft(conn)
    return RedirectResponse("/reconcile", status_code=303)


@router.post("/reconcile/{txn_id}/toggle")
def reconcile_toggle(request: Request, txn_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if repo.get_txn(conn, txn_id) is None:
        raise HTTPException(404)
    if repo.get_draft(conn) is None:
        raise HTTPException(400, "No reconcile in progress")
    repo.toggle_verified(conn, txn_id)
    return render(request, "_reconcile_toggled.html", {"row": repo.get_txn(conn, txn_id), "state": _state(conn)})


@router.post("/reconcile/finish")
def reconcile_finish(conn: sqlite3.Connection = Depends(get_conn)):
    draft = repo.get_draft(conn)
    if draft is None:
        return RedirectResponse("/reconcile", status_code=303)
    try:
        sid = repo.finish_statement(conn, *draft)
    except ValueError:
        return RedirectResponse("/reconcile", status_code=303)
    return RedirectResponse(f"/reconcile?done={sid}", status_code=303)
