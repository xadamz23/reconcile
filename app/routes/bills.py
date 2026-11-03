import json
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app import repo
from app.money import format_cents, parse_cents, plain_amount
from app.routes.deps import get_conn, is_htmx, masthead_context, render
from app.routes.transactions import DONE_HEADERS

router = APIRouter()

# Bills coming out within this many days say how soon.
SOON_DAYS = 7

SEQ_LABELS = {1: "1st of 2", 2: "2nd of 2"}


def _month(today: date) -> str:
    return today.strftime("%Y-%m")


def _row(bill: sqlite3.Row, month: str, today: date) -> dict:
    due_iso = repo.bill_due_date(bill, month, bill["seq"])
    due = date.fromisoformat(due_iso) if due_iso else None
    added = bill["txn_id"] is not None
    on = date.fromisoformat(bill["txn_date"]) if added else due
    return {
        "id": bill["id"],
        "seq": bill["seq"],
        "seq_label": SEQ_LABELS[bill["seq"]] if bill["times"] == 2 else None,
        "payee": bill["payee"],
        "ask": bool(bill["ask"]),
        "added": added,
        # A reconcile has ticked or locked it, so the tick can't take it back out.
        "locked": added and bill["txn_status"] != "unverified",
        "date_label": f"{on:%b} {on.day}" if on else None,  # the page is headed with the month, so no year
        "number": bill["txn_number"] if added else None,
        "cents": abs(bill["txn_cents"]) if added else bill["amount_cents"],
        "days": (due - today).days if due else None,
        # Date order; a bill that asks for its date goes after the dated ones until it's added.
        "sort": (on.day if on else 99, bill["payee"].casefold(), bill["id"], bill["seq"]),
    }


def _body_context(conn: sqlite3.Connection, today: date) -> dict:
    month = _month(today)
    rows = sorted((_row(b, month, today) for b in repo.bills_for_month(conn, month)), key=lambda r: r["sort"])
    return {
        "bills": rows,
        "month_name": f"{today:%B}",
        "added_count": sum(r["added"] for r in rows),
        "left_cents": sum(r["cents"] for r in rows if not r["added"]),
        "soon_days": SOON_DAYS,
    }


def _body(request: Request, conn: sqlite3.Connection, toast: dict | None = None, headers: dict | None = None):
    ctx = _body_context(conn, date.today())
    response = render(request, "_bills_body.html", {**ctx, **masthead_context(conn), "oob_totals": True, "toast": toast})
    response.headers.update(headers or {})
    return response


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


def _day(value: str) -> int:
    value = value.strip()
    return int(value) if value.isascii() and value.isdigit() else 0


def _parse(amount: str, payee: str, day: str, times: str, day2: str, mode: str) -> tuple[repo.BillInput | None, dict]:
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
    count = 2 if times == "2" else 1
    ask = mode == "ask"
    day_number, day2_number = _day(day), _day(day2)
    if not ask:
        if not 1 <= day_number <= 31:
            errors["day"] = "Enter a day from 1 to 31"
        if count == 2 and not 1 <= day2_number <= 31:
            errors["day2"] = "Enter a day from 1 to 31"
    if errors:
        return None, errors
    return repo.BillInput(
        payee, cents, day_number or None, count, day2_number if count == 2 and not ask else None, ask
    ), {}


@router.get("/bills/new")
def new_bill(request: Request):
    return _form_response(request, {"amount": "", "payee": "", "day": "", "times": "1", "day2": "", "mode": "now"})


@router.get("/bills/{bill_id}/edit")
def edit_bill(request: Request, bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    form = {
        "amount": plain_amount(bill["amount_cents"]),
        "payee": bill["payee"],
        "day": bill["day"],
        "times": str(bill["times"]),
        "day2": bill["day2"] or "",
        "mode": "ask" if bill["ask"] else "now",
    }
    return _form_response(request, form, bill)


@router.post("/bills")
@router.post("/bills/{bill_id}")
def save_bill(
    request: Request,
    bill_id: int | None = None,
    amount: str = Form(""),
    payee: str = Form(""),
    day: str = Form(""),
    times: str = Form("1"),
    day2: str = Form(""),
    mode: str = Form("now"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    bill = _require(conn, bill_id) if bill_id is not None else None
    data, errors = _parse(amount, payee, day, times, day2, mode)
    if errors:
        form = {"amount": amount, "payee": payee, "day": day, "times": times, "day2": day2, "mode": mode}
        return _form_response(request, form, bill, errors)
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


def _seq(bill: sqlite3.Row, seq: int) -> int:
    if not 1 <= seq <= bill["times"]:
        raise HTTPException(404)
    return seq


def _add_form(request: Request, bill: sqlite3.Row, seq: int, form: dict, errors: dict | None = None):
    ctx = {"bill": bill, "seq": seq, "seq_label": SEQ_LABELS[seq] if bill["times"] == 2 else None,
           "month_name": f"{date.today():%B}", "form": form, "errors": errors or {}}
    return render(request, "_bill_add_form.html", ctx)


@router.get("/bills/{bill_id}/add")
def add_sheet(request: Request, bill_id: int, seq: int = Query(1), conn: sqlite3.Connection = Depends(get_conn)):
    """The sheet a bill that asks opens when ticked: date, check number and amount."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    form = {"amount": plain_amount(bill["amount_cents"]), "date": date.today().isoformat(),
            "number": repo.next_check_number(conn)}
    return _add_form(request, bill, seq, form)


@router.post("/bills/{bill_id}/add")
def add_to_register(
    request: Request,
    bill_id: int,
    seq: int = Form(1),
    amount: str | None = Form(None),
    on_date: str | None = Form(None, alias="date"),
    number: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """A plain tick adds the bill as it is. The sheet sends date, number and amount too."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    cents = None
    if on_date is not None:  # from the sheet
        errors = {}
        try:
            cents = abs(parse_cents(amount or ""))
            if cents == 0:
                errors["amount"] = "Amount can't be zero"
        except ValueError:
            errors["amount"] = "Enter an amount like 9.99"
        try:
            date.fromisoformat(on_date)
        except ValueError:
            errors["date"] = "Pick a date"
        if errors:
            form = {"amount": amount or "", "date": on_date, "number": number}
            response = _add_form(request, bill, seq, form, errors)
            response.headers.update({"HX-Retarget": "#panel-body", "HX-Reswap": "innerHTML"})
            return response
    toast = None
    if repo.add_bill_txn(conn, bill_id, _month(date.today()), seq, on_date, cents, number):
        toast = {"bill_id": bill_id, "seq": seq,
                 "text": f"Added {bill['payee']}, −{format_cents(cents or bill['amount_cents'])}"}
    # From the sheet, close it; the swap into #bills-body shows the toast.
    headers = {"HX-Trigger": json.dumps({"panel-close": True})} if on_date is not None else None
    return _body(request, conn, toast, headers)


@router.post("/bills/{bill_id}/remove")
def remove_from_register(request: Request, bill_id: int, seq: int = Form(1), conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    repo.remove_bill_txn(conn, bill_id, _month(date.today()), _seq(bill, seq))
    return _body(request, conn)
