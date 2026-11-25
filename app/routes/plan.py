"""Plan: a worksheet of what might come out before payday, taken off the register balance
to see what's left. It opens in its own small window from the desktop Bills page
(app.js), and plan.js saves the whole list on every change, so closing the window loses
nothing."""

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from app import repo
from app.money import MAX_CENTS
from app.routes.deps import get_conn, render

router = APIRouter()


class LineIn(BaseModel):
    label: str = Field("", max_length=100)
    cents: int | None = Field(None, ge=0, le=MAX_CENTS)
    note: str = Field("", max_length=200)


class PlanIn(BaseModel):
    lines: list[LineIn] = Field(max_length=100)


@router.get("/plan")
def plan(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    if not repo.is_setup(conn):
        return RedirectResponse("/setup", status_code=303)
    lines = [{"label": r["label"], "cents": r["amount_cents"], "note": r["note"] or ""} for r in repo.plan_lines(conn)]
    return render(request, "plan.html", {"balance": repo.balance(conn), "lines": lines})


@router.post("/plan")
def save_plan(body: PlanIn, conn: sqlite3.Connection = Depends(get_conn)):
    repo.save_plan(conn, [repo.PlanLine(line.label, line.cents, line.note) for line in body.lines])
    return Response(status_code=204)


@router.get("/plan/balance")
def plan_balance(conn: sqlite3.Connection = Depends(get_conn)):
    """Read when the window gets focus again, after a payment was added in the register."""
    return {"cents": repo.balance(conn)}
