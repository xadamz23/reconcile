import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import repo
from app.money import parse_cents
from app.routes.deps import get_conn, is_htmx, masthead_context, render

router = APIRouter()

# Rows per page. The ledger ends with a placeholder that loads the next page when
# it scrolls into view (see _ledger_rows.html).
PAGE_SIZE = 100


def parse_cursor(text: str) -> tuple[str, int] | None:
    """'2026-10-10:42' (date and id of the last row shown) -> ('2026-10-10', 42)."""
    day, _, txn_id = text.partition(":")
    try:
        return date.fromisoformat(day).isoformat(), int(txn_id)
    except ValueError:
        return None


def register_context(
    conn: sqlite3.Connection,
    kind: str,
    q: str,
    amount: str,
    show_reconciled: bool,
    before: tuple[str, int] | None = None,
    limit: int = PAGE_SIZE,
) -> dict:
    kind = kind if kind in repo.KINDS else "all"
    error = None
    try:
        # One extra row says whether there's another page.
        rows = repo.list_txns(conn, kind=kind, q=q, amount=amount, show_reconciled=show_reconciled,
                              before=before, limit=limit + 1)
    except ValueError:
        rows, error = [], f"“{amount}” isn't an amount. Try 45, 9.99 or a range like 10-50."
    more, rows = len(rows) > limit, rows[:limit]
    return {
        "rows": rows,
        "next_cursor": f"{rows[-1]['date']}:{rows[-1]['id']}" if more else None,
        "before_date": before[0] if before else None,
        **masthead_context(conn),
        "filters": {"kind": kind, "q": q, "amount": amount, "show_reconciled": show_reconciled},
        "filtering": bool(q.strip() or amount.strip() or kind != "all" or show_reconciled),
        "filter_error": error,
    }


@router.get("/")
def register(
    request: Request,
    kind: str = "all",
    q: str = "",
    amount: str = "",
    show_reconciled: bool = False,
    before: str = "",
    rows: int = 0,
    conn: sqlite3.Connection = Depends(get_conn),
):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    cursor = parse_cursor(before) if before else None
    if before and cursor is None:
        raise HTTPException(400, "Bad page cursor")
    # After a save the ledger refetches as many rows as it's showing (`rows`), so
    # the list doesn't snap back to the first page. Next-page requests get `rows`
    # too (htmx merges hx-vals from every ancestor) and must ignore it.
    limit = PAGE_SIZE if cursor else max(PAGE_SIZE, rows)
    ctx = register_context(conn, kind, q, amount, show_reconciled, cursor, limit)
    if is_htmx(request) and cursor is not None:
        return render(request, "_ledger_rows.html", ctx)
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
