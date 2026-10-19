#!/usr/bin/env python3
"""One-off import of transactions exported (as CSV) from the old register app.

Standard library only, so it runs with the host's python3 against the DB file
in the Docker volume. Dry run by default; pass --apply to write.

    python3 import_register_csv.py export.csv /path/to/reconcile.db            # preview
    python3 import_register_csv.py export.csv /path/to/reconcile.db --apply    # import

Expected columns: Date (YYYYMMDD), Amount (signed), Status (Verified/Unverified),
Name, Number or code, Note. Balance, Type and Category are ignored.

Rows already in the DB (same date, payee, amount and number) are skipped, so
re-running is safe. The register must already be set up (opening balance).
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple, Optional

SCHEMA_VERSION = 1  # app/db.py MIGRATIONS this script was written against
STATUSES = {"unverified": "unverified", "verified": "verified"}


class Row(NamedTuple):
    date: str
    payee: str
    amount_cents: int
    number: Optional[str]
    note: Optional[str]
    status: str

    def key(self) -> tuple:
        return (self.date, self.payee.lower(), self.amount_cents, self.number)


@dataclass
class Plan:
    to_import: list = field(default_factory=list)
    already_present: list = field(default_factory=list)
    opening_date: str = ""
    balance_after: int = 0
    verified_after: int = 0


def clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def parse_csv(path: Path) -> list:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for line_no, rec in enumerate(csv.DictReader(f), start=2):
            try:
                date = datetime.strptime(rec["Date"].strip(), "%Y%m%d").date().isoformat()
                amount = Decimal(rec["Amount"].strip().replace(",", "").replace("$", ""))
                if amount != amount.quantize(Decimal("0.01")):
                    raise InvalidOperation
                status = STATUSES[rec["Status"].strip().lower()]
                payee = clean(rec["Name"])
                if not payee:
                    raise ValueError("blank Name")
            except (KeyError, ValueError, InvalidOperation) as e:
                sys.exit(f"error: line {line_no}: can't read row ({e!r}): {rec}")
            rows.append(Row(date, payee, int(amount * 100), clean(rec.get("Number or code")),
                            clean(rec.get("Note")), status))
    return rows


def connect(db_path: Path) -> sqlite3.Connection:
    try:
        db_path.stat()
    except FileNotFoundError:
        sys.exit(f"error: no database at {db_path}")
    except PermissionError:
        # e.g. /var/lib/docker is root-only, so other users can't even see the file.
        sys.exit(f"error: permission denied reaching {db_path}; run as root, then chown the DB files back")
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        sys.exit(f"error: DB schema is version {version}, this script expects {SCHEMA_VERSION}")
    return conn


def plan_import(conn: sqlite3.Connection, rows: list) -> Plan:
    opening = conn.execute("SELECT date FROM transactions WHERE is_opening = 1").fetchone()
    if opening is None:
        sys.exit("error: set up the register (opening balance) in the app before importing")
    existing = Counter(
        (r["date"], r["payee"].lower(), r["amount_cents"], r["number"])
        for r in conn.execute("SELECT date, payee, amount_cents, number FROM transactions")
    )
    plan = Plan(opening_date=opening["date"])
    for row in rows:
        if existing[row.key()] > 0:
            existing[row.key()] -= 1
            plan.already_present.append(row)
        else:
            plan.to_import.append(row)
    balance, verified = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0), "
        "COALESCE(SUM(CASE WHEN status != 'unverified' THEN amount_cents END), 0) FROM transactions"
    ).fetchone()
    plan.balance_after = balance + sum(r.amount_cents for r in plan.to_import)
    plan.verified_after = verified + sum(r.amount_cents for r in plan.to_import if r.status != "unverified")
    return plan


def apply_plan(conn: sqlite3.Connection, plan: Plan) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT INTO transactions (date, payee, amount_cents, number, note, status) VALUES (?, ?, ?, ?, ?, ?)",
            [(r.date, r.payee, r.amount_cents, r.number, r.note, r.status) for r in plan.to_import],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def run(csv_path: Path, db_path: Path, apply: bool) -> Plan:
    rows = parse_csv(Path(csv_path))
    conn = connect(Path(db_path))
    try:
        plan = plan_import(conn, rows)
        if apply and plan.to_import:
            apply_plan(conn, plan)
    finally:
        conn.close()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    parser.add_argument("db", type=Path)
    parser.add_argument("--apply", action="store_true", help="write to the database (default: dry run)")
    args = parser.parse_args()

    plan = run(args.csv, args.db, args.apply)
    imported = plan.to_import
    print(f"Rows in CSV:           {len(imported) + len(plan.already_present)}")
    print(f"Already in register:   {len(plan.already_present)} (skipped)")
    label = "Imported:" if args.apply else "To import:"
    print(f"{label:<23}{len(imported)}  totalling {money(sum(r.amount_cents for r in imported))}")
    print(f"Balance after:         {money(plan.balance_after)}")
    print(f"Verified after:        {money(plan.verified_after)}")
    early = [r for r in imported if r.date < plan.opening_date]
    if early:
        print(f"warning: {len(early)} row(s) dated before the opening balance ({plan.opening_date}); "
              f"their running balances will look wrong. Edit the opening balance's date to {min(r.date for r in early)} or earlier.")
    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply to import.")


if __name__ == "__main__":
    main()
