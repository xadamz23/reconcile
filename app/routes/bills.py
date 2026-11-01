import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app import repo
from app.money import format_cents, parse_cents, plain_amount
from app.routes.deps import get_conn, is_htmx, masthead_context, render
from app.routes.transactions import DONE_HEADERS

router = APIRouter()

# Bills coming out within this many days say how soon.
SOON_DAYS = 7


def _row(bill: sqlite3.Row, month: str, today: date) -> dict:
    due = date.fromisoformat(repo.bill_date(bill["day"], month))
    added = bill["txn_id"] is not None
    days = (due - today).days
    on = date.fromisoformat(bill["txn_date"]) if added else due
    return {
        "id": bill["id"],
        "payee": bill["payee"],
        "added": added,
        # A reconcile has ticked or locked it, so the tick can't take it back out.
        "locked": added and bill["txn_status"] != "unverified",
        "date_label": f"{on:%b} {on.day}",  # the page is headed with the month, so no year
        "cents": abs(bill["txn_cents"]) if added else bill["amount_cents"],
        "days": days,
    }


def _body_context(conn: sqlite3.Connection, today: date) -> dict:
    month = today.strftime("%Y-%m")
    rows = [_row(b, month, today) for b in repo.bills_for_month(conn, month)]
    return {
        "bills": rows,
        "month_name": f"{today:%B}",
        "added_count": sum(r["added"] for r in rows),
        "left_cents": sum(r["cents"] for r in rows if not r["added"]),
        "soon_days": SOON_DAYS,
    }


def _body(request: Request, conn: sqlite3.Connection, toast: dict | None = None):
    ctx = _body_context(conn, date.today())
    return render(request, "_bills_body.html", {**ctx, **masthead_context(conn), "oob_totals": True, "toast": toast})


@router.get("/bills")
def bills(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    if is_htmx(request) and request.headers.get("HX-Target") == "bills-body":
        return _body(request, conn)
    return render(request, "bills.html", {**_body_context(conn, date.today()), **masthead_context(conn)})


def _require(conn, bill_id: int) -> sqlite3.Row:
    bill = repo.get_bill(conn, bill_id)
    if bill is None:
        raise HTTPException(404)
    return bill


def _form_response(request: Request, form: dict, bill: sqlite3.Row | None = None, errors: dict | None = None):
    return render(request, "_bill_form.html", {"form": form, "bill": bill, "errors": errors or {}})


def _parse(amount: str, payee: str, day: str) -> tuple[repo.BillInput | None, dict]:
    errors = {}
    cents = 0
    try:
        cents = abs(parse_cents(amount))
        if cents == 0:
            errors["amount"] = "Amount can't be zero"
    except ValueError:
        errors["amount"] = "Enter an amount like 9.99"
    if not payee.strip():
        errors["payee"] = "Who is the bill paid to?"
    day_number = int(day) if day.strip().isascii() and day.strip().isdigit() else 0
    if not 1 <= day_number <= 31:
        errors["day"] = "Enter a day from 1 to 31"
    if errors:
        return None, errors
    return repo.BillInput(payee, cents, day_number), {}


@router.get("/bills/new")
def new_bill(request: Request):
    return _form_response(request, {"amount": "", "payee": "", "day": ""})


@router.get("/bills/{bill_id}/edit")
def edit_bill(request: Request, bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    form = {"amount": plain_amount(bill["amount_cents"]), "payee": bill["payee"], "day": bill["day"]}
    return _form_response(request, form, bill)


@router.post("/bills")
@router.post("/bills/{bill_id}")
def save_bill(
    request: Request,
    bill_id: int | None = None,
    amount: str = Form(""),
    payee: str = Form(""),
    day: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    bill = _require(conn, bill_id) if bill_id is not None else None
    data, errors = _parse(amount, payee, day)
    if errors:
        return _form_response(request, {"amount": amount, "payee": payee, "day": day}, bill, errors)
    if bill is None:
        repo.create_bill(conn, data)
    else:
        repo.update_bill(conn, bill_id, data)
    return Response(status_code=200, headers=DONE_HEADERS)


@router.post("/bills/{bill_id}/delete")
def delete_bill(bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    _require(conn, bill_id)
    repo.delete_bill(conn, bill_id)
    return Response(status_code=200, headers=DONE_HEADERS)


@router.post("/bills/{bill_id}/add")
def add_to_register(request: Request, bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    toast = None
    if repo.add_bill_txn(conn, bill_id, date.today().strftime("%Y-%m")):
        toast = {"bill_id": bill_id, "text": f"Added {bill['payee']}, −{format_cents(bill['amount_cents'])}"}
    return _body(request, conn, toast)


@router.post("/bills/{bill_id}/remove")
def remove_from_register(request: Request, bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    _require(conn, bill_id)
    repo.remove_bill_txn(conn, bill_id, date.today().strftime("%Y-%m"))
    return _body(request, conn)
