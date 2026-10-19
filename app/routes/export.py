import csv
import io
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app import repo
from app.money import plain_amount
from app.routes.deps import get_conn

router = APIRouter()


@router.get("/export.csv")
def export_csv(conn: sqlite3.Connection = Depends(get_conn)):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "payee", "amount", "number", "note", "status", "running_balance"])
    for t in repo.all_txns_for_export(conn):
        sign = "-" if t["amount_cents"] < 0 else ""
        run_sign = "-" if t["running_cents"] < 0 else ""
        writer.writerow([
            t["date"], t["payee"], sign + plain_amount(t["amount_cents"]),
            t["number"] or "", t["note"] or "", t["status"],
            run_sign + plain_amount(t["running_cents"]),
        ])
    filename = f"reconcile-{date.today().isoformat()}.csv"
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
