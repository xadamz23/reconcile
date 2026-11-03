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

# Closes the form panel after a save from the sheet, which swaps #bills-body itself.
CLOSE_PANEL = {"HX-Trigger": json.dumps({"panel-close": True})}


def _month(today: date) -> str:
    return today.strftime("%Y-%m")


def _label(on: date) -> str:
    return f"{on:%b} {on.day}"  # the page is headed with the month, so no year


def _row(bill: sqlite3.Row, month: str, today: date) -> dict:
    due_iso = repo.bill_due_date(bill, month, bill["seq"])
    due = date.fromisoformat(due_iso) if due_iso else None
    added = bill["txn_id"] is not None
    state = "added" if added else bill["mark"] or "open"  # open, added, paid, skipped
    on = date.fromisoformat(bill["txn_date"]) if added else due
    if state == "paid":
        on = date.fromisoformat(bill["marked_on"])
    return {
        "id": bill["id"],
        "seq": bill["seq"],
        "seq_label": SEQ_LABELS[bill["seq"]] if bill["times"] == 2 else None,
        "payee": bill["payee"],
        "ask": bool(bill["ask"]),
        "elsewhere": bool(bill["elsewhere"]),
        "sometimes": bool(bill["sometimes"]),
        "state": state,
        # A reconcile has ticked or locked it, so the tick can't take it back out.
        "locked": added and bill["txn_status"] != "unverified",
        "date_label": _label(on) if on else None,
        "number": bill["txn_number"] if added else None,
        "cents": abs(bill["txn_cents"]) if added else bill["amount_cents"],  # None: it varies
        "days": (due - today).days if due else None,
        # Date order, with paid and nothing-due rows at the bottom. A bill that asks for its
        # date goes after the dated ones until it's added.
        "sort": (state in ("paid", "skipped"), on.day if on else 99, bill["payee"].casefold(), bill["id"], bill["seq"]),
    }


def _body_context(conn: sqlite3.Connection, today: date) -> dict:
    month = _month(today)
    rows = sorted((_row(b, month, today) for b in repo.bills_for_month(conn, month)), key=lambda r: r["sort"])
    return {
        "bills": rows,
        "month_name": f"{today:%B}",
        "done_count": sum(r["state"] != "open" for r in rows),
        # Only money leaving this account; a bill whose amount varies adds nothing.
        "left_cents": sum(r["cents"] or 0 for r in rows if r["state"] == "open" and not r["elsewhere"]),
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


# --- The bill form ---


def _form_response(request: Request, form: dict, bill: sqlite3.Row | None = None, errors: dict | None = None):
    return render(request, "_bill_form.html", {"form": form, "bill": bill, "errors": errors or {}})


def _day(value: str) -> int:
    value = value.strip()
    return int(value) if value.isascii() and value.isdigit() else 0


def _parse(form: dict) -> tuple[repo.BillInput | None, dict]:
    errors = {}
    elsewhere = form["source"] == "other"
    ask = form["mode"] == "ask" and not elsewhere
    count = 2 if form["times"] == "2" else 1
    cents = None
    if form["amount"].strip():
        try:
            cents = abs(parse_cents(form["amount"])) or None
        except ValueError:
            errors["amount"] = "Enter an amount like 9.99"
    if cents is None and not errors and not (ask or elsewhere):
        errors["amount"] = "Enter the amount, or pick Ask if it varies"
    if not form["payee"].strip():
        errors["payee"] = "Who is the bill paid to?"
    day, day2 = _day(form["day"]), _day(form["day2"])
    if not ask:
        if not 1 <= day <= 31:
            errors["day"] = "Enter a day from 1 to 31"
        if count == 2 and not 1 <= day2 <= 31:
            errors["day2"] = "Enter a day from 1 to 31"
    if errors:
        return None, errors
    return repo.BillInput(
        form["payee"], cents, day or None, count, day2 if count == 2 and not ask else None,
        ask, elsewhere, form["sometimes"] == "1",
    ), {}


@router.get("/bills/new")
def new_bill(request: Request):
    form = {"amount": "", "payee": "", "day": "", "times": "1", "day2": "", "mode": "now", "source": "here",
            "sometimes": ""}
    return _form_response(request, form)


@router.get("/bills/{bill_id}/edit")
def edit_bill(request: Request, bill_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    form = {
        "amount": plain_amount(bill["amount_cents"]) if bill["amount_cents"] else "",
        "payee": bill["payee"],
        "day": bill["day"] or "",
        "times": str(bill["times"]),
        "day2": bill["day2"] or "",
        "mode": "ask" if bill["ask"] else "now",
        "source": "other" if bill["elsewhere"] else "here",
        "sometimes": "1" if bill["sometimes"] else "",
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
    source: str = Form("here"),
    sometimes: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    bill = _require(conn, bill_id) if bill_id is not None else None
    form = {"amount": amount, "payee": payee, "day": day, "times": times, "day2": day2, "mode": mode,
            "source": source, "sometimes": sometimes}
    data, errors = _parse(form)
    if errors:
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


# --- Ticking a bill off for the month ---


def _seq(bill: sqlite3.Row, seq: int) -> int:
    if not 1 <= seq <= bill["times"]:
        raise HTTPException(404)
    return seq


def _sheet(request: Request, conn, bill: sqlite3.Row, seq: int, form: dict, errors: dict | None = None):
    ctx = {"bill": bill, "seq": seq, "seq_label": SEQ_LABELS[seq] if bill["times"] == 2 else None,
           "month_name": f"{date.today():%B}", "form": form, "errors": errors or {},
           "next_number": repo.next_check_number(conn)}
    return render(request, "_bill_add_form.html", ctx)


@router.get("/bills/{bill_id}/add")
def add_sheet(request: Request, bill_id: int, seq: int = Query(1), conn: sqlite3.Connection = Depends(get_conn)):
    """What the tick opens for a bill that asks or isn't due every month: the date, amount
    and check number (or Mark paid, for another account), and Nothing due if it can skip."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    due = repo.bill_due_date(bill, _month(date.today()), seq)
    form = {"amount": plain_amount(bill["amount_cents"]) if bill["amount_cents"] else "",
            "date": due or date.today().isoformat(), "number": ""}
    return _sheet(request, conn, bill, seq, form)


def _toast(bill: sqlite3.Row, seq: int, text: str, undo: str) -> dict:
    return {"bill_id": bill["id"], "seq": seq, "text": text, "undo": undo}


@router.post("/bills/{bill_id}/add")
def add_to_register(
    request: Request,
    bill_id: int,
    seq: int = Form(1),
    sheet: str = Form(""),
    amount: str = Form(""),
    on_date: str = Form("", alias="date"),
    number: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """A plain tick adds the bill as it is. The sheet (sheet=1) sends the date, amount and
    check number too."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    cents = None
    if sheet:
        errors = {}
        try:
            cents = abs(parse_cents(amount))
            if cents == 0:
                errors["amount"] = "Amount can't be zero"
        except ValueError:
            errors["amount"] = "Enter an amount like 9.99"
        try:
            date.fromisoformat(on_date)
        except ValueError:
            errors["date"] = "Pick a date"
        if errors:
            response = _sheet(request, conn, bill, seq, {"amount": amount, "date": on_date, "number": number}, errors)
            response.headers.update({"HX-Retarget": "#panel-body", "HX-Reswap": "innerHTML"})
            return response
    elif bill["ask"] or bill["sometimes"] or bill["elsewhere"] or not bill["amount_cents"]:
        raise HTTPException(400, "This bill is added from its sheet")  # a stale page
    toast = None
    if repo.add_bill_txn(conn, bill_id, _month(date.today()), seq, on_date or None, cents, number):
        toast = _toast(bill, seq, f"Added {bill['payee']}, −{format_cents(cents or bill['amount_cents'])}", "remove")
    return _body(request, conn, toast, CLOSE_PANEL if sheet else None)


@router.post("/bills/{bill_id}/remove")
def remove_from_register(request: Request, bill_id: int, seq: int = Form(1), conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    repo.remove_bill_txn(conn, bill_id, _month(date.today()), _seq(bill, seq))
    return _body(request, conn)


@router.post("/bills/{bill_id}/mark")
def mark(
    request: Request,
    bill_id: int,
    seq: int = Form(1),
    kind: str = Form(...),
    sheet: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Mark the month done without adding anything: paid (another account) or skipped
    (nothing due)."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    if kind not in ("paid", "skipped") or (kind == "paid" and not bill["elsewhere"]) or (
            kind == "skipped" and not bill["sometimes"]):
        raise HTTPException(400)
    today = date.today()
    toast = None
    if repo.mark_bill(conn, bill_id, _month(today), seq, kind):
        text = f"Marked {bill['payee']} paid" if kind == "paid" else f"Nothing due for {bill['payee']}"
        toast = _toast(bill, seq, text, "unmark")
    return _body(request, conn, toast, CLOSE_PANEL if sheet else None)


@router.post("/bills/{bill_id}/unmark")
def unmark(request: Request, bill_id: int, seq: int = Form(1), conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    repo.unmark_bill(conn, bill_id, _month(date.today()), _seq(bill, seq))
    return _body(request, conn)
