"""The Overview (/): the desktop landing page. Balance less the bills still to add, the
balance over time, money in and out by month, this month's bills and spending, and
anything that needs a look. Charts are server-drawn SVG and CSS, with no chart library."""

import calendar
import json
import sqlite3
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.money import format_cents
from app.routes import bills, spending
from app.routes.deps import get_conn, masthead_context, render

router = APIRouter()

CHART_DAYS = 90  # balance history, today included
FLOW_MONTHS = 6
UPCOMING = 6  # open bills listed
TOP_PAYEES = 5
STALE_CHECK_DAYS = 30  # an uncleared check older than this is worth a look
RECONCILE_EVERY = 28  # days after a statement's closing date before nudging for the next

# Query parameters the register used to take at "/". Links and bookmarks with them go there.
REGISTER_PARAMS = {"q", "kind", "amount", "show_reconciled", "before", "rows"}


def _short(on: date) -> str:
    return f"{on:%b} {on.day}"


def _k(cents: int) -> str:
    """Compact dollars for chart labels: $850, $5.2k."""
    dollars = abs(cents) / 100
    sign = "−" if cents < 0 else ""
    if dollars >= 1000:
        return f"{sign}${dollars / 1000:.1f}k"
    return f"{sign}${dollars:.0f}"


def _nice_step(span: int) -> int:
    """A round gridline step (1, 2, 2.5 or 5 times a power of ten, in cents) giving about 4 lines."""
    raw = max(span / 4, 100)
    power = 10 ** (len(str(int(raw))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if m * power >= raw:
            return int(m * power)
    return 10 * power


def _axis_label(cents: int) -> str:
    dollars = cents / 100
    sign = "−" if dollars < 0 else ""
    dollars = abs(dollars)
    if dollars >= 1000:
        return f"{sign}${dollars / 1000:g}k"
    return f"{sign}${dollars:g}"


def _days_label(days: int | None) -> str:
    if days is None:
        return "asks for the date"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days > 0:
        return f"in {days} days"
    return "yesterday" if days == -1 else f"{-days} days ago"


def balance_chart(conn: sqlite3.Connection, today: date, bill_rows: list[dict]) -> dict | None:
    """The last CHART_DAYS of end-of-day balances, then a projection to the month's end:
    future-dated transactions plus the bills still to add, each on its day (overdue or
    undated ones tomorrow). Coordinates are percentages of the plot."""
    start = today - timedelta(days=CHART_DAYS - 1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    days = repo.daily_balances(conn, start.isoformat(), month_end.isoformat())
    tomorrow = today + timedelta(days=1)
    pending = []  # (day, cents) for open bills that come out of this account
    for r in bill_rows:
        if r["state"] == "open" and not r["elsewhere"] and r["cents"]:
            due = date.fromisoformat(r["due"]) if r["due"] else tomorrow
            pending.append((max(due, tomorrow), r["cents"]))

    points = []
    for iso, cents in days:
        on = date.fromisoformat(iso)
        if on > today:
            cents -= sum(c for d, c in pending if d <= on)
        points.append({"date": on, "cents": cents, "proj": on > today})

    values = [p["cents"] for p in points]
    step = _nice_step(max(values) - min(values))
    lo = (min(values) // step) * step
    hi = -((-max(values)) // step) * step
    if hi == lo:
        hi = lo + step
    last = len(points) - 1

    def x(i: int) -> float:
        return round(100 * i / last, 3)

    def y(cents: int) -> float:
        return round(100 * (hi - cents) / (hi - lo), 3)

    for i, p in enumerate(points):
        p["x"], p["y"] = x(i), y(p["cents"])
    hist = [p for p in points if not p["proj"]]
    proj = [hist[-1]] + [p for p in points if p["proj"]]

    def path(ps: list[dict]) -> str:
        return " ".join(f"{'L' if i else 'M'}{p['x']} {p['y']}" for i, p in enumerate(ps))

    stmt = repo.last_statement(conn)
    rec = None
    if stmt:
        end = date.fromisoformat(stmt["end_date"])
        if start <= end <= today:
            rec = {"x": x((end - start).days), "label": f"Reconciled through {_short(end)}"}
    return {
        "line": path(hist),
        "area": f"{path(hist)} L{hist[-1]['x']} 100 L0 100 Z",
        "proj": path(proj) if len(proj) > 1 else None,
        "grid": [{"y": y(v), "label": _axis_label(v)} for v in range(lo, hi + 1, step)],
        "months": [{"x": p["x"], "label": f"{p['date']:%b}"} for p in points[1:] if p["date"].day == 1],
        "rec": rec,
        "now": hist[-1],
        "end": proj[-1] if len(proj) > 1 else None,
        "end_label": f"{_short(proj[-1]['date'])} · {format_cents(proj[-1]['cents'])}",
        # For the hover readout in app.js: x, y, date, amount, projected.
        "data": json.dumps([[p["x"], p["y"], _short(p["date"]), format_cents(p["cents"]), int(p["proj"])]
                            for p in points], separators=(",", ":")),
    }


def flow_chart(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Money in and out for the last FLOW_MONTHS months, this one so far included."""
    first = today.year * 12 + today.month - 1 - (FLOW_MONTHS - 1)
    months = [date(m // 12, m % 12 + 1, 1) for m in range(first, first + FLOW_MONTHS)]
    rows = {r["month"]: r for r in repo.monthly_flow(conn, months[0].isoformat(), today.isoformat())}
    data = [(m, rows[k]["in_cents"] if (k := f"{m:%Y-%m}") in rows else 0,
             rows[k]["out_cents"] if k in rows else 0) for m in months]
    biggest = max([max(i, o) for _, i, o in data] + [1])
    return [{
        "label": f"{m:%b}",
        "name": f"{m:%B}" + (" so far" if m.month == today.month and m.year == today.year else ""),
        "partial": m.month == today.month and m.year == today.year,
        "in_cents": i, "out_cents": o, "net_cents": i - o,
        "in_h": f"{80 * i / biggest:.1f}%", "out_h": f"{80 * o / biggest:.1f}%",
        "in_k": "+" + _k(i), "out_k": "−" + _k(o),
        "net_k": ("+" if i >= o else "−") + _k(abs(i - o)),
    } for m, i, o in data]


def attention(conn: sqlite3.Connection, today: date, bill_rows: list[dict]) -> list[dict]:
    items = []
    draft = repo.get_draft(conn)
    stmt = repo.last_statement(conn)
    if draft:
        items.append({"icon": "scale", "title": "A reconcile is in progress",
                      "sub": f"Statement closing {_short(date.fromisoformat(draft[0]))}",
                      "href": "/reconcile", "link": "Continue reconciling"})
    elif stmt is None or (today - date.fromisoformat(stmt["end_date"])).days >= RECONCILE_EVERY:
        sub = (f"The last statement closed {_short(date.fromisoformat(stmt['end_date']))}" if stmt
               else "No statement reconciled yet")
        items.append({"icon": "scale", "title": "Time to reconcile", "sub": sub,
                      "href": "/reconcile", "link": "Reconcile a statement"})
    for r in bill_rows:
        if r["state"] == "open" and r["days"] is not None and r["days"] < 0:
            items.append({"icon": "repeat", "warn": True, "title": f"{r['payee']} was due {r['date_label']}",
                          "sub": "Not added or marked yet", "href": "/bills", "link": "Open Bills"})
    cutoff = (today - timedelta(days=STALE_CHECK_DAYS)).isoformat()
    for t in repo.old_uncleared_checks(conn, cutoff):
        age = (today - date.fromisoformat(t["date"])).days
        items.append({"icon": "clock", "warn": True, "title": f"Check {t['number']} hasn't cleared",
                      "sub": f"{t['payee']} · {format_cents(-t['amount_cents'])} · written {age} days ago",
                      "href": "/register?" + urlencode({"q": t["payee"], "show_reconciled": "true"}),
                      "link": "Find it"})
    for r in bill_rows:
        if r["state"] == "open" and not r["elsewhere"] and r["cents"] is None:
            items.append({"icon": "repeat", "title": f"{r['payee']}'s amount varies",
                          "sub": "It isn't in Free after bills until you add it"})
    return items


def overview_context(conn: sqlite3.Connection, today: date) -> dict:
    month = bills.month_context(conn, today)
    rows = month["bills"]
    here = [r for r in rows if not r["elsewhere"] and r["state"] != "skipped"]
    added = sum(r["cents"] or 0 for r in here if r["state"] == "added")
    left = month["left_cents"]
    balance = repo.balance(conn)
    stmt = repo.last_statement(conn)
    uncleared = repo.uncleared_summary(conn)
    spend = spending.spending_context(conn, "month", today)
    top, rest = spend["top"][:TOP_PAYEES], spend["top"][TOP_PAYEES:] + spend["rest"]
    return {
        "balance": balance,
        "month_change": balance - repo.balance_on(conn, (today.replace(day=1) - timedelta(days=1)).isoformat()),
        "month_start": _short(today.replace(day=1)),
        "free_cents": balance - left,
        "left_cents": left,
        "left_count": sum(1 for r in here if r["state"] == "open" and r["cents"]),
        "uncleared": uncleared,
        "oldest_label": _short(date.fromisoformat(uncleared["oldest"])) if uncleared["oldest"] else None,
        "stmt": stmt,
        "stmt_label": _short(date.fromisoformat(stmt["end_date"])) if stmt else None,
        "stmt_days": (today - date.fromisoformat(stmt["end_date"])).days if stmt else None,
        "month_name": month["month_name"],
        "bill_count": len(rows),
        "done_count": month["done_count"],
        "added_cents": added,
        "bill_pct": f"{100 * added / (added + left):.1f}%" if added + left else "100%",
        "upcoming": [{**r, "when": _days_label(r["days"]),
                      "weekday": f"{date.fromisoformat(r['due']):%a}" if r["due"] else None,
                      "day_num": date.fromisoformat(r["due"]).day if r["due"] else None,
                      "soon": r["days"] is not None and r["days"] <= 2}
                     for r in rows if r["state"] == "open"][:UPCOMING],
        "chart": balance_chart(conn, today, rows),
        "flow": flow_chart(conn, today),
        "spend_total": spend["total_cents"],
        "spend_top": top,
        "spend_rest": {"count": len(rest), "total_cents": sum(i["total_cents"] for i in rest),
                       "bar": f"{min(100, 100 * sum(i['total_cents'] for i in rest) / top[0]['total_cents']):.1f}%"}
        if rest else None,
        "attention": attention(conn, today, rows),
    }


@router.get("/")
def overview(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    if REGISTER_PARAMS & set(request.query_params):
        return RedirectResponse(f"/register?{request.url.query}", status_code=303)
    return render(request, "overview.html", {**overview_context(conn, date.today()), **masthead_context(conn)})
