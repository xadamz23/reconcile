import json
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app import repo
from app.money import parse_cents, plain_amount
from app.routes.deps import get_conn, render

router = APIRouter()

# Tells the page to close the form panel and refresh the ledger (see static/js/app.js).
DONE_HEADERS = {
    "HX-Trigger": json.dumps({"ledger-changed": True, "panel-close": True}),
    "HX-Reswap": "none",
}


def _form_from_txn(t: sqlite3.Row) -> dict:
    return {
        "sign": "-" if t["amount_cents"] < 0 else "+",
        "amount": plain_amount(t["amount_cents"]),
        "payee": t["payee"],
        "date": t["date"],
        "number": t["number"] or "",
        "note": t["note"] or "",
        "verified": t["status"] != "unverified",
    }


def _form_response(request: Request, form: dict, txn: sqlite3.Row | None = None, errors: dict | None = None):
    return render(request, "_txn_form.html", {"form": form, "txn": txn, "errors": errors or {}})


def _parse(sign, amount, payee, on_date, number, note, verified) -> tuple[repo.TxnInput | None, dict]:
    errors = {}
    cents = 0
    try:
        cents = abs(parse_cents(amount))
        if cents == 0:
            errors["amount"] = "Amount can't be zero"
    except ValueError:
        errors["amount"] = "Enter an amount like 9.99"
    if not payee.strip():
        errors["payee"] = "Who was it paid to or from?"
    try:
        date.fromisoformat(on_date)
    except ValueError:
        errors["date"] = "Pick a date"
    if errors:
        return None, errors
    signed = cents if sign == "+" else -cents
    return repo.TxnInput(on_date, payee, signed, verified == "1", number, note), {}


def _require(conn, txn_id: int) -> sqlite3.Row:
    txn = repo.get_txn(conn, txn_id)
    if txn is None:
        raise HTTPException(404)
    return txn


@router.get("/txn/new")
def new_txn(request: Request):
    form = {"sign": "-", "amount": "", "payee": "", "date": date.today().isoformat(),
            "number": "", "note": "", "verified": False}
    return _form_response(request, form)


@router.get("/txn/{txn_id}/edit")
def edit_txn(request: Request, txn_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    txn = _require(conn, txn_id)
    return _form_response(request, _form_from_txn(txn), txn)


@router.post("/txn")
@router.post("/txn/{txn_id}")
def save_txn(
    request: Request,
    txn_id: int | None = None,
    sign: str = Form("-"),
    amount: str = Form(""),
    payee: str = Form(""),
    on_date: str = Form("", alias="date"),
    number: str = Form(""),
    note: str = Form(""),
    verified: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    txn = _require(conn, txn_id) if txn_id is not None else None
    data, errors = _parse(sign, amount, payee, on_date, number, note, verified)
    if errors:
        form = {"sign": sign, "amount": amount, "payee": payee, "date": on_date,
                "number": number, "note": note, "verified": verified == "1"}
        return _form_response(request, form, txn, errors)
    if txn is None:
        repo.create_txn(conn, data)
    else:
        repo.update_txn(conn, txn_id, data)
    return Response(status_code=200, headers=DONE_HEADERS)


@router.post("/txn/{txn_id}/delete")
def delete_txn(txn_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    txn = _require(conn, txn_id)
    if txn["is_opening"]:
        raise HTTPException(400, "The opening balance can be edited but not deleted")
    repo.delete_txn(conn, txn_id)
    return Response(status_code=200, headers=DONE_HEADERS)


@router.post("/txn/{txn_id}/toggle")
def toggle_txn(request: Request, txn_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    _require(conn, txn_id)
    repo.toggle_verified(conn, txn_id)
    balance, verified = repo.totals(conn)
    ctx = {"row": repo.get_txn_with_running(conn, txn_id), "balance": balance, "verified": verified}
    return render(request, "_row_toggled.html", ctx)


@router.get("/payees")
def payees(q: str = "", conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse(repo.suggest_payees(conn, q))
