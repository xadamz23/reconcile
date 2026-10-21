import sqlite3
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.routes.deps import get_conn, is_htmx, render

router = APIRouter()

PERIODS = {"month": "This month", "3m": "3 months", "year": "This year"}

# Payees listed one by one; the rest fold into "Everything else" (unless that's just one).
TOP = 10


def period_range(period: str, today: date) -> tuple[str, str]:
    """Calendar periods ending today: this month, it plus the two before, or the year so far."""
    if period == "year":
        start = today.replace(month=1, day=1)
    elif period == "3m":
        months = today.year * 12 + today.month - 1 - 2
        start = date(months // 12, months % 12 + 1, 1)
    else:
        start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def _range_label(start: str, end: str) -> str:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a.year != b.year:
        return f"{a:%b} {a.day}, {a.year} – {b:%b} {b.day}, {b.year}"
    return f"{a:%b} {a.day} – {b:%b} {b.day}, {b.year}"


def _share(part: int, whole: int) -> str:
    pct = round(100 * part / whole)
    return "<1%" if pct == 0 else f"{pct}%"


def spending_context(conn: sqlite3.Connection, period: str, today: date) -> dict:
    period = period if period in PERIODS else "month"
    start, end = period_range(period, today)
    rows = repo.spending_by_payee(conn, start, end)
    total = sum(r["total_cents"] for r in rows)
    biggest = rows[0]["total_cents"] if rows else 0

    def item(payee: str, cents: int, n: int, href: str | None = None) -> dict:
        # Bars are scaled to the biggest payee, so the top one fills its track.
        return {"payee": payee, "total_cents": cents, "n": n, "href": href,
                "share": _share(cents, total), "bar": f"{min(100, 100 * cents / biggest):.1f}%"}

    items = [
        item(r["payee"], r["total_cents"], r["n"],
             "/?" + urlencode({"q": r["payee"], "kind": "payments", "show_reconciled": "true"}))
        for r in rows
    ]
    top, rest = (items[:TOP], items[TOP:]) if len(items) > TOP + 1 else (items, [])
    return {
        "period": period,
        "periods": PERIODS,
        "range_label": _range_label(start, end),
        "total_cents": total,
        "payee_count": len(items),
        "payment_count": sum(i["n"] for i in items),
        "top": top,
        "rest": rest,
        "rest_item": item("Everything else", sum(i["total_cents"] for i in rest),
                          sum(i["n"] for i in rest)) if rest else None,
    }


@router.get("/spending")
def spending(request: Request, period: str = "month", conn: sqlite3.Connection = Depends(get_conn)):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    ctx = spending_context(conn, period, date.today())
    if is_htmx(request) and request.headers.get("HX-Target") == "spending-body":
        return render(request, "_spending_body.html", ctx)
    return render(request, "spending.html", ctx)
