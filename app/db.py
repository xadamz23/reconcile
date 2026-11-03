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
    """
    CREATE TABLE bills (
        id           INTEGER PRIMARY KEY,
        payee        TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        day          INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    ALTER TABLE transactions ADD COLUMN bill_id INTEGER REFERENCES bills(id) ON DELETE SET NULL;
    ALTER TABLE transactions ADD COLUMN bill_month TEXT;
    CREATE UNIQUE INDEX ix_transactions_bill ON transactions(bill_id, bill_month) WHERE bill_id IS NOT NULL;
    """,
    # Bills paid twice a month (day2), and bills that ask for the date, check number and
    # amount when ticked (ask=1, which ignores day and day2). bill_seq says which of the
    # month's payments a transaction is.
    """
    ALTER TABLE bills ADD COLUMN times INTEGER NOT NULL DEFAULT 1 CHECK (times IN (1, 2));
    ALTER TABLE bills ADD COLUMN day2 INTEGER CHECK (day2 BETWEEN 1 AND 31);
    ALTER TABLE bills ADD COLUMN ask INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE transactions ADD COLUMN bill_seq INTEGER;
    UPDATE transactions SET bill_seq = 1 WHERE bill_month IS NOT NULL;
    DROP INDEX ix_transactions_bill;
    CREATE UNIQUE INDEX ix_transactions_bill ON transactions(bill_id, bill_month, bill_seq) WHERE bill_id IS NOT NULL;
    """,
    # Rebuilds bills so amount (a bill whose amount varies) and day (a bill that asks for
    # its date) can be empty, and adds elsewhere (paid from another account: the tick marks
    # it paid and adds nothing) and sometimes (not due every month: the tick opens a sheet
    # that can mark the month "nothing due"). bill_marks holds those paid / nothing-due
    # marks, one per bill payment per month.
    """
    CREATE TABLE bills_new (
        id           INTEGER PRIMARY KEY,
        payee        TEXT NOT NULL,
        amount_cents INTEGER CHECK (amount_cents IS NULL OR amount_cents > 0),
        day          INTEGER CHECK (day BETWEEN 1 AND 31),
        times        INTEGER NOT NULL DEFAULT 1 CHECK (times IN (1, 2)),
        day2         INTEGER CHECK (day2 BETWEEN 1 AND 31),
        ask          INTEGER NOT NULL DEFAULT 0,
        elsewhere    INTEGER NOT NULL DEFAULT 0,
        sometimes    INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO bills_new (id, payee, amount_cents, day, times, day2, ask, created_at, updated_at)
    SELECT id, payee, amount_cents, CASE WHEN ask THEN NULL ELSE day END, times, day2, ask, created_at, updated_at
      FROM bills;
    DROP TABLE bills;
    ALTER TABLE bills_new RENAME TO bills;
    CREATE TABLE bill_marks (
        bill_id   INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
        month     TEXT NOT NULL,
        seq       INTEGER NOT NULL,
        kind      TEXT NOT NULL CHECK (kind IN ('paid', 'skipped')),
        marked_on TEXT NOT NULL,
        PRIMARY KEY (bill_id, month, seq)
    );
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
    # Foreign keys are off while migrating so a table can be rebuilt (create, copy, drop,
    # rename) without the drop firing ON DELETE actions. SQLite ignores this pragma inside a
    # transaction, so it's set around each script, and the keys are checked before commit.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {i};")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                conn.execute("ROLLBACK")
                raise RuntimeError(f"Migration {i} broke a foreign key")
            conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
