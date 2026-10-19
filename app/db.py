import sqlite3
from pathlib import Path

# Each entry migrates the schema from version N to N+1. Append only; never edit.
MIGRATIONS = [
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE statements (
        id                   INTEGER PRIMARY KEY,
        end_date             TEXT NOT NULL,
        ending_balance_cents INTEGER NOT NULL,
        reconciled_at        TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE transactions (
        id           INTEGER PRIMARY KEY,
        date         TEXT NOT NULL,
        payee        TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        number       TEXT,
        note         TEXT,
        status       TEXT NOT NULL DEFAULT 'unverified'
                     CHECK (status IN ('unverified', 'verified', 'reconciled')),
        statement_id INTEGER REFERENCES statements(id),
        is_opening   INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_transactions_date ON transactions(date, id);
    CREATE INDEX ix_transactions_payee ON transactions(payee COLLATE NOCASE);
    """,
]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {i}; COMMIT;")
