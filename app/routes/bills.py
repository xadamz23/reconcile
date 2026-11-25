import calendar
import json
import re
import sqlite3
from datetime import date, timedelta

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


def _first(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def _shift(month: str, by: int) -> str:
    """The month by months after month (YYYY-MM)."""
    n = int(month[:4]) * 12 + int(month[5:7]) - 1 + by
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def _last_day(month: str) -> date:
    first = _first(month)
    return first.replace(day=calendar.monthrange(first.year, first.month)[1])


def _overdue_month(conn: sqlite3.Connection, today: date) -> str | None:
    """Last month, if its leftovers can show as overdue: only from bills_overdue_from on
    (set when this arrived), so older months never flood the page."""
    prev = _shift(_month(today), -1)
    start = repo.get_setting(conn, "bills_overdue_from")
    return prev if start and prev >= start else None


def _target_month(conn: sqlite3.Connection, value: str, today: date) -> str:
    """The month a bill action is for: this month, next month, or last month while its
    leftovers show as overdue. Anything else is a stale or made-up request."""
    current = _month(today)
    value = value or current
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise HTTPException(400, "Bad month")
    if value in (current, _shift(current, 1)) or value == _overdue_month(conn, today):
        return value
    raise HTTPException(400, "That month can't be changed from here")


def mid_payday(month: str) -> date:
    """Roughly when the mid-month paycheck lands: the 15th, or the Friday before when the
    15th is on a weekend. Only a visual guide on the Bills page."""
    day = _first(month).replace(day=15)
    return day - timedelta(days=max(0, day.weekday() - 4))


def _label(on: date) -> str:
    return f"{on:%b} {on.day}"  # the page is headed with the month, so no year


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _schedule(bill: sqlite3.Row) -> str:
    """When the bill comes out, for the desktop cards: 'Every month on the 10th'."""
    if bill["day"] is None:
        when = "Asks for the date and amount"
    elif bill["times"] == 2:
        when = f"the {_ordinal(bill['day'])} and {_ordinal(bill['day2'])}"
        when = f"Some months, {when}" if bill["sometimes"] else f"Every month, {when}"
    else:
        when = f"{'Some months' if bill['sometimes'] else 'Every month'} on the {_ordinal(bill['day'])}"
    if bill["ask"] and bill["day"] is not None:
        when += ", amount varies" if bill["amount_cents"] is None else ", asks for the amount"
    return f"Another account, {when[0].lower()}{when[1:]}" if bill["elsewhere"] else when


def _status(state: str, days: int | None, date_label: str | None, month: str, today: date) -> tuple[str, str]:
    """The desktop card's pill: (text, tone) for where the bill stands this month."""
    if state == "added":
        return f"In register · {date_label}", "done"
    if state == "paid":
        return "Paid from another account", "done"
    if state == "skipped":
        return "Nothing due this month", ""
    if days is None:  # no usual day
        current = _month(today)
        if month < current:
            return f"Not added in {_first(month):%B}", "warn"
        return ("Any day this month" if month == current else f"Any day in {_first(month):%B}"), ""
    if days < 0:
        return (f"Was due {date_label}" if month < _month(today) else "Date passed"), "warn"
    if days == 0:
        return "Today", "soon"
    return ("Tomorrow" if days == 1 else f"In {days} days"), "soon" if days <= 3 else ""


def _row(bill: sqlite3.Row, month: str, today: date) -> dict:
    due_iso = repo.bill_due_date(bill, month, bill["seq"])
    due = date.fromisoformat(due_iso) if due_iso else None
    added = bill["txn_id"] is not None
    state = "added" if added else bill["mark"] or "open"  # open, added, paid, skipped
    on = date.fromisoformat(bill["txn_date"]) if added else due
    if state == "paid":
        on = date.fromisoformat(bill["marked_on"])
    days = (due - today).days if due else None
    date_label = _label(on) if on else None
    status, tone = _status(state, days, date_label, month, today)
    return {
        "id": bill["id"],
        "seq": bill["seq"],
        "month": month,
        "seq_label": SEQ_LABELS[bill["seq"]] if bill["times"] == 2 else None,
        "payee": bill["payee"],
        "ask": bool(bill["ask"]),
        "elsewhere": bool(bill["elsewhere"]),
        "sometimes": bool(bill["sometimes"]),
        "state": state,
        # A reconcile has ticked or locked it, so the tick can't take it back out.
        "locked": added and bill["txn_status"] != "unverified",
        "date_label": date_label,
        "number": bill["txn_number"] if added else None,
        "cents": abs(bill["txn_cents"]) if added else bill["amount_cents"],  # None: it varies
        "days": days,
        "due": due_iso,  # None when the bill asks for its date
        # For the desktop cards: the date tile, the schedule line and the status pill.
        "day_num": on.day if on else None,
        "weekday": f"{on:%a}".upper() if on else None,
        "schedule": _schedule(bill),
        "status": status,
        "tone": tone,
        # Date order, with paid and nothing-due rows at the bottom. A bill that asks for its
        # date goes after the dated ones until it's added.
        "created": bill["created_at"][:10],
        "sort": (state in ("paid", "skipped"), on.day if on else 99, bill["payee"].casefold(), bill["id"], bill["seq"]),
    }


def _rail(rows: list[dict], today: date) -> dict:
    """The desktop month rail: each bill as a dot on its day, and today. Positions are
    percentages along the month; bills on the same day stack upward."""
    last = calendar.monthrange(today.year, today.month)[1]

    def x(day: int) -> str:
        return f"{100 * (day - 1) / (last - 1):.2f}%"

    stacked: dict[int, int] = {}
    dots = []
    for r in sorted(rows, key=lambda r: (r["day_num"] or 0, r["sort"])):
        day = int(r["due"][8:]) if r["due"] else r["day_num"] if r["state"] != "open" else None
        if day is None:
            continue  # asks for its date and isn't added yet
        level = stacked[day] = stacked.get(day, -1) + 1
        late = r["state"] == "open" and r["days"] is not None and r["days"] < 0
        dots.append({"row": r, "x": x(day), "level": level, "day": day,
                     "tone": "skipped" if r["state"] == "skipped" else "done" if r["state"] != "open" else "late" if late else ""})
    ticks = sorted({1, 5, 10, 15, 20, 25, last})
    return {"dots": dots, "today": x(today.day), "ticks": [{"x": x(d), "day": d} for d in ticks]}


def _rows(conn: sqlite3.Connection, month: str, today: date) -> list[dict]:
    return sorted((_row(b, month, today) for b in repo.bills_for_month(conn, month)), key=lambda r: r["sort"])


def _next_month(conn: sqlite3.Connection, today: date) -> dict:
    """Next month's bills, so they can go in the register early (paid from this paycheck).
    One list in date order, done ones in place, with the mid-month payday marked: rows
    before it are the ones this paycheck covers."""
    month = _shift(_month(today), 1)
    payday = mid_payday(month)
    rows = sorted((_row(b, month, today) for b in repo.bills_for_month(conn, month)),
                  key=lambda r: (r["day_num"] or 99, r["payee"].casefold(), r["id"], r["seq"]))
    before = [r for r in rows if r["due"] and r["due"] < payday.isoformat()]
    return {
        "month": month,
        "name": f"{_first(month):%B}",
        "rows": rows,
        "split": len(before),  # the payday rule goes before rows[split]
        "payday_label": f"Payday, around {payday:%a} {payday:%b} {payday.day}",
        "before_cents": sum(r["cents"] or 0 for r in before if r["state"] == "open" and not r["elsewhere"]),
        "done_count": sum(r["state"] != "open" for r in rows),
    }


def _overdue(conn: sqlite3.Connection, today: date) -> dict | None:
    """Last month's bills that were never added or marked, if that month counts (see
    _overdue_month). A bill made after a payment's date doesn't owe it."""
    month = _overdue_month(conn, today)
    if month is None:
        return None
    rows = []
    for r in _rows(conn, month, today):
        if r["state"] != "open":
            continue
        if r["created"] <= (r["due"] or _last_day(month).isoformat()):
            rows.append(r)
    return {"month": month, "name": f"{_first(month):%B}", "rows": rows} if rows else None


def month_context(conn: sqlite3.Connection, today: date) -> dict:
    month = _month(today)
    rows = _rows(conn, month, today)
    overdue = _overdue(conn, today)
    return {
        "bills": rows,
        "month_name": f"{today:%B}",
        "done_count": sum(r["state"] != "open" for r in rows),
        # Only money leaving this account; a bill whose amount varies adds nothing.
        "left_cents": sum(r["cents"] or 0 for r in rows if r["state"] == "open" and not r["elsewhere"]),
        "soon_days": SOON_DAYS,
        "open_rows": [r for r in rows if r["state"] == "open"],
        "done_rows": [r for r in rows if r["state"] != "open"],
        "rail": _rail(rows, today) if rows else None,
        "next": _next_month(conn, today) if rows else None,
        "overdue": overdue,
        # Last month's leftovers that come out of this account and have an amount.
        "overdue_cents": sum(r["cents"] or 0 for r in overdue["rows"] if not r["elsewhere"]) if overdue else 0,
    }


def _body(request: Request, conn: sqlite3.Connection, toast: dict | None = None, headers: dict | None = None):
    ctx = month_context(conn, date.today())
    response = render(request, "_bills_body.html", {**ctx, **masthead_context(conn), "oob_totals": True, "toast": toast})
    response.headers.update(headers or {})
    return response


@router.get("/bills")
def bills(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    if is_htmx(request) and request.headers.get("HX-Target") == "bills-body":
        return _body(request, conn)
    return render(request, "bills.html", {**month_context(conn, date.today()), **masthead_context(conn)})


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
    # A bill that asks can leave its days blank (no usual day), but not just one of two.
    blank = not form["day"].strip() and (count == 1 or not form["day2"].strip())
    if not (ask and blank):
        if not 1 <= day <= 31:
            errors["day"] = "Enter a day from 1 to 31"
        if count == 2 and not 1 <= day2 <= 31:
            errors["day2"] = "Enter a day from 1 to 31"
    if errors:
        return None, errors
    return repo.BillInput(
        form["payee"], cents, day or None, count, (day2 or None) if count == 2 else None,
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


def _sheet(request: Request, conn, bill: sqlite3.Row, seq: int, month: str, form: dict, errors: dict | None = None):
    ctx = {"bill": bill, "seq": seq, "seq_label": SEQ_LABELS[seq] if bill["times"] == 2 else None,
           "month": month, "month_name": f"{_first(month):%B}", "is_current": month == _month(date.today()),
           "form": form, "errors": errors or {},
           "next_number": repo.next_check_number(conn)}
    return render(request, "_bill_add_form.html", ctx)


@router.get("/bills/{bill_id}/add")
def add_sheet(request: Request, bill_id: int, seq: int = Query(1), month: str = Query(""),
              conn: sqlite3.Connection = Depends(get_conn)):
    """What the tick opens for a bill that asks or isn't due every month: the date, amount
    and check number (or Mark paid, for another account), and Nothing due if it can skip."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    month = _target_month(conn, month, date.today())
    due = repo.bill_due_date(bill, month, seq)
    form = {"amount": plain_amount(bill["amount_cents"]) if bill["amount_cents"] else "",
            "date": due or date.today().isoformat(), "number": ""}
    return _sheet(request, conn, bill, seq, month, form)


def _toast(bill: sqlite3.Row, seq: int, month: str, text: str, undo: str) -> dict:
    return {"bill_id": bill["id"], "seq": seq, "month": month, "text": text, "undo": undo}


@router.post("/bills/{bill_id}/add")
def add_to_register(
    request: Request,
    bill_id: int,
    seq: int = Form(1),
    month: str = Form(""),
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
    month = _target_month(conn, month, date.today())
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
            response = _sheet(request, conn, bill, seq, month, {"amount": amount, "date": on_date, "number": number}, errors)
            response.headers.update({"HX-Retarget": "#panel-body", "HX-Reswap": "innerHTML"})
            return response
    elif bill["ask"] or bill["sometimes"] or bill["elsewhere"] or not bill["amount_cents"]:
        raise HTTPException(400, "This bill is added from its sheet")  # a stale page
    toast = None
    if repo.add_bill_txn(conn, bill_id, month, seq, on_date or None, cents, number):
        toast = _toast(bill, seq, month, f"Added {bill['payee']}, −{format_cents(cents or bill['amount_cents'])}", "remove")
    return _body(request, conn, toast, CLOSE_PANEL if sheet else None)


@router.post("/bills/{bill_id}/remove")
def remove_from_register(request: Request, bill_id: int, seq: int = Form(1), month: str = Form(""),
                         conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    repo.remove_bill_txn(conn, bill_id, _target_month(conn, month, date.today()), _seq(bill, seq))
    return _body(request, conn)


@router.post("/bills/{bill_id}/mark")
def mark(
    request: Request,
    bill_id: int,
    seq: int = Form(1),
    month: str = Form(""),
    kind: str = Form(...),
    sheet: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Mark the month done without adding anything: paid (another account) or skipped
    (nothing due, or dismissing last month's leftover)."""
    bill = _require(conn, bill_id)
    seq = _seq(bill, seq)
    today = date.today()
    month = _target_month(conn, month, today)
    past = month < _month(today)
    if kind not in ("paid", "skipped") or (kind == "paid" and not bill["elsewhere"]) or (
            kind == "skipped" and not (bill["sometimes"] or past)):
        raise HTTPException(400)
    toast = None
    if repo.mark_bill(conn, bill_id, month, seq, kind):
        if kind == "paid":
            text = f"Marked {bill['payee']} paid"
        elif past:
            text = f"Dismissed {bill['payee']} for {_first(month):%B}"
        else:
            text = f"Nothing due for {bill['payee']}"
        toast = _toast(bill, seq, month, text, "unmark")
    return _body(request, conn, toast, CLOSE_PANEL if sheet else None)


@router.post("/bills/{bill_id}/unmark")
def unmark(request: Request, bill_id: int, seq: int = Form(1), month: str = Form(""),
           conn: sqlite3.Connection = Depends(get_conn)):
    bill = _require(conn, bill_id)
    repo.unmark_bill(conn, bill_id, _target_month(conn, month, date.today()), _seq(bill, seq))
    return _body(request, conn)
