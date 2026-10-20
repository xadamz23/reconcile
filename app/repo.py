"""All SQL lives here. Functions take a sqlite3 connection opened by app.db."""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date as Date


OPENING_PAYEE = "Opening balance"
KINDS = ("all", "payments", "deposits", "unverified")


def _like_contains(text: str) -> str:
    """LIKE pattern matching text anywhere, with wildcards escaped (use ESCAPE '\\')."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def clean_text(value: str | None) -> str | None:
    """Strip surrounding whitespace; blank strings become None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass
class TxnInput:
    date: str
    payee: str
    amount_cents: int
    verified: bool = False
    number: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        self.payee = clean_text(self.payee) or ""
        self.number = clean_text(self.number)
        self.note = clean_text(self.note)
        if not self.payee:
            raise ValueError("Payee is required")
        Date.fromisoformat(self.date)  # raises ValueError if malformed


# --- Settings / setup ---


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, clean_text(value) or ""),
    )


def is_setup(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None


def setup(conn: sqlite3.Connection, account_name: str, opening_cents: int, on_date: str) -> None:
    Date.fromisoformat(on_date)
    with conn:
        conn.execute("BEGIN")
        set_setting(conn, "account_name", account_name or "Checking")
        conn.execute(
            "INSERT INTO transactions (date, payee, amount_cents, status, is_opening) "
            "VALUES (?, ?, ?, 'reconciled', 1)",
            (on_date, OPENING_PAYEE, opening_cents),
        )


# --- Transactions ---


def create_txn(conn: sqlite3.Connection, t: TxnInput) -> int:
    cur = conn.execute(
        "INSERT INTO transactions (date, payee, amount_cents, number, note, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (t.date, t.payee, t.amount_cents, t.number, t.note, "verified" if t.verified else "unverified"),
    )
    return cur.lastrowid


def update_txn(conn: sqlite3.Connection, txn_id: int, t: TxnInput) -> None:
    # Reconciled items stay reconciled; the verified checkbox only applies to the others.
    conn.execute(
        """
        UPDATE transactions
           SET date = ?, payee = ?, amount_cents = ?, number = ?, note = ?,
               status = CASE WHEN status = 'reconciled' THEN status ELSE ? END,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (t.date, t.payee, t.amount_cents, t.number, t.note,
         "verified" if t.verified else "unverified", txn_id),
    )


def delete_txn(conn: sqlite3.Connection, txn_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))


def get_txn(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()


def toggle_verified(conn: sqlite3.Connection, txn_id: int) -> str | None:
    conn.execute(
        """
        UPDATE transactions
           SET status = CASE status WHEN 'unverified' THEN 'verified'
                                    WHEN 'verified' THEN 'unverified'
                                    ELSE status END,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (txn_id,),
    )
    row = get_txn(conn, txn_id)
    return row["status"] if row else None


def totals(conn: sqlite3.Connection) -> tuple[int, int]:
    """(balance, verified balance) in cents. Balance includes future-dated items."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS balance,
               COALESCE(SUM(CASE WHEN status != 'unverified' THEN amount_cents END), 0) AS verified
          FROM transactions
        """
    ).fetchone()
    return row["balance"], row["verified"]


_PARTIAL_AMOUNT_RE = re.compile(r"^(\d*)(?:\.(\d{0,2}))?$")


def _partial_amount(text: str) -> tuple[int, int]:
    """The cents range a partly typed amount covers.

    Whole dollars cover that dollar ('45' -> 45.00-45.99), one cents digit covers
    ten cents ('20.0' -> 20.00-20.09), and two cents digits are exact.
    """
    cleaned = text.strip().replace("$", "").replace(",", "").replace(" ", "")
    match = _PARTIAL_AMOUNT_RE.match(cleaned)
    if not match or not (match.group(1) or match.group(2)):
        raise ValueError(f"Not an amount: {text!r}")
    whole, frac = match.group(1), match.group(2) or ""
    base = int(whole or "0") * 100 + int(frac.ljust(2, "0"))
    return base, base + (99, 9, 0)[len(frac)]


def parse_amount_filter(text: str) -> tuple[int | None, int | None]:
    """Search box amount -> (min, max) absolute cents; None means unbounded.

    '45' / '20.0' / '20.08' are partial amounts (see _partial_amount).
    '10-50' is a range whose ends are partial too (10.00-50.99); '10-' and '-50' are open.
    """
    text = text.strip()
    if "-" in text:
        lo_s, hi_s = text.split("-", 1)
        lo = _partial_amount(lo_s)[0] if lo_s.strip() else None
        hi = _partial_amount(hi_s)[1] if hi_s.strip() else None
        if lo is None and hi is None:
            raise ValueError("Empty amount range")
        return lo, hi
    return _partial_amount(text)


def list_txns(
    conn: sqlite3.Connection,
    kind: str = "all",
    q: str | None = None,
    amount: str | None = None,
    show_reconciled: bool = False,
) -> list[sqlite3.Row]:
    """Newest first. running_cents is computed over the whole ledger before filtering."""
    where, params = [], []
    if kind == "payments":
        where.append("amount_cents < 0")
    elif kind == "deposits":
        where.append("amount_cents > 0")
    elif kind == "unverified":
        where.append("status = 'unverified'")
    if not show_reconciled and kind != "unverified":
        where.append("status != 'reconciled'")
    if q := clean_text(q):
        where.append("payee LIKE ? ESCAPE '\\'")
        params.append(_like_contains(q))
    if amount := clean_text(amount):
        lo, hi = parse_amount_filter(amount)
        if lo is not None:
            where.append("ABS(amount_cents) >= ?")
            params.append(lo)
        if hi is not None:
            where.append("ABS(amount_cents) <= ?")
            params.append(hi)
    sql = """
        WITH ledger AS (
            SELECT *, SUM(amount_cents) OVER (ORDER BY date, id) AS running_cents
              FROM transactions
        )
        SELECT * FROM ledger
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date DESC, id DESC"
    return conn.execute(sql, params).fetchall()


def get_txn_with_running(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        WITH ledger AS (
            SELECT *, SUM(amount_cents) OVER (ORDER BY date, id) AS running_cents
              FROM transactions
        )
        SELECT * FROM ledger WHERE id = ?
        """,
        (txn_id,),
    ).fetchone()


def suggest_payees(conn: sqlite3.Connection, q: str | None, limit: int = 8) -> list[str]:
    """Distinct past payees (case-insensitive), prefix matches first, then by use count.

    The spelling returned is the most recently used one.
    """
    q = clean_text(q) or ""
    rows = conn.execute(
        """
        SELECT payee, COUNT(*) OVER w AS uses, MAX(date) OVER w AS last_used,
               ROW_NUMBER() OVER (PARTITION BY payee COLLATE NOCASE ORDER BY date DESC, id DESC) AS rn
          FROM transactions
         WHERE is_opening = 0 AND payee LIKE ? ESCAPE '\\'
        WINDOW w AS (PARTITION BY payee COLLATE NOCASE)
        """,
        (_like_contains(q),),
    ).fetchall()
    latest = [r for r in rows if r["rn"] == 1]
    needle = q.lower()
    # Two stable sorts: most recent first, then prefix matches and use count take priority.
    latest.sort(key=lambda r: r["last_used"], reverse=True)
    latest.sort(key=lambda r: (not r["payee"].lower().startswith(needle), -r["uses"]))
    return [r["payee"] for r in latest[:limit]]


# --- Statement reconciliation ---


@dataclass
class ReconcileState:
    end_date: str
    statement_cents: int
    cleared_cents: int
    candidates: list[sqlite3.Row] = field(default_factory=list)

    @property
    def difference_cents(self) -> int:
        return self.statement_cents - self.cleared_cents

    @property
    def balanced(self) -> bool:
        return self.difference_cents == 0


def reconcile_state(conn: sqlite3.Connection, end_date: str, statement_cents: int) -> ReconcileState:
    Date.fromisoformat(end_date)
    cleared = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) FROM transactions
         WHERE status = 'reconciled' OR (status = 'verified' AND date <= ?)
        """,
        (end_date,),
    ).fetchone()[0]
    candidates = conn.execute(
        "SELECT * FROM transactions WHERE status != 'reconciled' AND date <= ? ORDER BY date DESC, id DESC",
        (end_date,),
    ).fetchall()
    return ReconcileState(end_date, statement_cents, cleared, candidates)


def finish_statement(conn: sqlite3.Connection, end_date: str, statement_cents: int) -> int:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if not reconcile_state(conn, end_date, statement_cents).balanced:
            raise ValueError("Statement does not balance")
        sid = conn.execute(
            "INSERT INTO statements (end_date, ending_balance_cents) VALUES (?, ?)",
            (end_date, statement_cents),
        ).lastrowid
        conn.execute(
            "UPDATE transactions SET status = 'reconciled', statement_id = ?, updated_at = datetime('now') "
            "WHERE status = 'verified' AND date <= ?",
            (sid, end_date),
        )
    return sid


def last_statement(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM statements ORDER BY end_date DESC, id DESC LIMIT 1").fetchone()


def list_statements(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.*, COUNT(t.id) AS item_count
          FROM statements s LEFT JOIN transactions t ON t.statement_id = s.id
         GROUP BY s.id
         ORDER BY s.end_date DESC, s.id DESC
        """
    ).fetchall()


def all_txns_for_export(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *, SUM(amount_cents) OVER (ORDER BY date, id) AS running_cents
          FROM transactions ORDER BY date, id
        """
    ).fetchall()
