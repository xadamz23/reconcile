import mimetypes
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from app import repo, statement_match
from app.money import format_cents, parse_cents, plain_amount
from app.routes.deps import get_conn, is_htmx, locked_conn, masthead_context, render
from app.statement_ai import StatementReadError

router = APIRouter()

MAX_UPLOAD = 20 * 1024 * 1024


def _state(conn) -> repo.ReconcileState | None:
    draft = repo.get_draft(conn)
    return repo.reconcile_state(conn, *draft) if draft else None


def _review(conn, state: repo.ReconcileState | None) -> statement_match.Review | None:
    statement = repo.get_statement(conn) if state else None
    return statement_match.match(statement, state.candidates) if statement else None


def _body(conn, **extra) -> dict:
    """What _reconcile_body.html needs: the draft's state and, when a statement was read,
    its review with a payee suggestion for each line that isn't in the register."""
    state = _state(conn)
    review = _review(conn, state)
    suggest = {}
    if review:
        payees = repo.all_payees(conn)
        suggest = {i: statement_match.suggest_payee(review.lines[i]["payee"], payees)
                   for i in review.missing}
    return {"state": state, "review": review, "suggest": suggest, **extra}


def _page(request: Request, conn, form: dict | None = None, error: str | None = None, done: int | None = None,
          read_error: str | None = None):
    body = _body(conn)
    state = body["state"]
    if form is None and state:
        sign = "-" if state.statement_cents < 0 else ""
        form = {"end_date": state.end_date, "balance": sign + plain_amount(state.statement_cents)}
    elif form is None:
        form = {"end_date": date.today().isoformat(), "balance": ""}
    statement = body["review"].statement if body["review"] else None
    from_statement = bool(statement and state and statement["closing_date"] == state.end_date
                          and statement["ending_cents"] == state.statement_cents)
    return render(request, "reconcile.html", {
        **body,
        "form": form,
        "error": error,
        "read_error": read_error,
        "done": done,
        "ai_enabled": request.app.state.ai_enabled,
        "statement": statement,
        "from_statement": from_statement,
        "statements": repo.list_statements(conn),
        **masthead_context(conn),
    })


@router.get("/reconcile")
def reconcile(request: Request, done: int | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    if is_htmx(request) and request.headers.get("HX-Target") == "rec-body":
        return render(request, "_reconcile_body.html", _body(conn, oob_totals=True, **masthead_context(conn)))
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


@router.post("/reconcile/statement")
def reconcile_read_statement(request: Request, file: UploadFile = File(...)):
    """Read an uploaded statement with Claude and start the reconcile from it.

    Deliberately not Depends(get_conn): that would hold the shared database lock for the
    10 to 40 seconds Claude takes, and every other request would wait. The lock is taken
    only around the database work, after the read."""
    if not request.app.state.ai_enabled:
        raise HTTPException(404)
    data = file.file.read(MAX_UPLOAD + 1)
    filename = file.filename or "statement"
    media_type = (file.content_type or "").split(";")[0].strip().lower()
    if media_type in ("", "application/octet-stream"):
        media_type = mimetypes.guess_type(filename)[0] or ""
    error = None
    if not data:
        error = "Choose a statement file to read."
    elif len(data) > MAX_UPLOAD:
        error = "That file is over 20 MB. Try a PDF from the bank's website, or a smaller photo."
    else:
        try:
            statement = request.app.state.read_statement(data, media_type, filename)
        except StatementReadError as e:
            error = str(e)
    with locked_conn(request.app) as conn:
        if error:
            return _page(request, conn, read_error=error)
        repo.start_from_statement(conn, statement)
    return RedirectResponse("/reconcile", status_code=303)


def _require_review(conn):
    state = _state(conn)
    if state is None:
        raise HTTPException(400, "No reconcile in progress")
    review = _review(conn, state)
    if review is None:
        raise HTTPException(400, "No statement has been read")
    return review


@router.post("/reconcile/statement/apply")
def reconcile_apply(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    """Tick every register item the statement matched that isn't ticked yet."""
    review = _require_review(conn)
    ids = [t["id"] for t in review.ready]
    n = repo.set_verified_many(conn, ids)
    toast = {"text": f"Ticked {n} item{'' if n == 1 else 's'}",
             "undo": "/reconcile/statement/undo", "vals": {"ids": ",".join(map(str, ids))}} if n else None
    return render(request, "_reconcile_body.html", _body(conn, toast_oob=True, toast=toast, just_ticked=set(ids)))


@router.post("/reconcile/statement/undo")
def reconcile_undo_apply(request: Request, ids: str = Form(""), conn: sqlite3.Connection = Depends(get_conn)):
    """Undo for Tick matched: untick those items again."""
    if repo.get_draft(conn) is None:
        raise HTTPException(400, "No reconcile in progress")
    for tid in ids.split(","):
        if tid.strip().isdigit():
            repo.set_verified(conn, int(tid), False)
    return render(request, "_reconcile_body.html", _body(conn, toast_oob=True, toast=None))


@router.post("/reconcile/statement/fix")
def reconcile_fix(
    request: Request,
    txn: int = Form(...),
    line: int = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """An "Amount differs" pair: change the register item's amount to the statement's and
    tick it. Only a pair the review still shows can be fixed."""
    review = _require_review(conn)
    if review.differs_by_txn.get(txn) != line:
        raise HTTPException(409, "That item no longer differs from the statement")
    row = repo.get_txn(conn, txn)
    cents = review.lines[line]["cents"]
    repo.update_txn(conn, txn, repo.TxnInput(row["date"], row["payee"], cents, False, row["number"], row["note"]))
    repo.set_verified(conn, txn, True)
    toast = {"text": "Changed", "strong": row["payee"], "after": f"to {format_cents(abs(cents))} and ticked it"}
    return render(request, "_reconcile_body.html", _body(conn, toast_oob=True, toast=toast, just_ticked={txn}))


@router.post("/reconcile/discard")
def reconcile_discard(conn: sqlite3.Connection = Depends(get_conn)):
    repo.discard_draft(conn)
    return RedirectResponse("/reconcile", status_code=303)


@router.post("/reconcile/{txn_id}/toggle")
def reconcile_toggle(
    request: Request,
    txn_id: int,
    verified: str = Form(...),
    undo: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Tick (verified=1) or untick (verified=0) an item, then redraw the body so it moves
    between the To check and On statement groups. The toast offers Undo, except after an Undo."""
    if repo.get_txn(conn, txn_id) is None:
        raise HTTPException(404)
    if repo.get_draft(conn) is None:
        raise HTTPException(400, "No reconcile in progress")
    ticked = verified == "1"
    repo.set_verified(conn, txn_id, ticked)
    row = repo.get_txn(conn, txn_id)
    toast = None if undo else {"row": row, "ticked": ticked}
    return render(request, "_reconcile_body.html", _body(
        conn, toast_oob=True, toast=toast, just_ticked={txn_id} if ticked else set(),
    ))


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
