import pytest

from app import repo
from app.repo import TxnInput


def add(conn, date, payee, cents, status="unverified", **kw):
    return repo.create_txn(
        conn,
        TxnInput(date=date, payee=payee, amount_cents=cents, verified=status != "unverified", **kw),
    )


@pytest.fixture
def ledger(conn):
    repo.setup(conn, "Adam's Checking", 100000, "2026-10-01")
    add(conn, "2026-10-05", "Fozzys", -4531)
    add(conn, "2026-10-03", "Paycheck", 200000, status="verified")
    add(conn, "2026-10-10", "SiriusXM", -999)
    return conn


def test_setup_creates_reconciled_opening_balance(conn):
    assert not repo.is_setup(conn)
    repo.setup(conn, "  Adam's Checking  ", 100000, "2026-10-01")
    assert repo.is_setup(conn)
    assert repo.get_setting(conn, "account_name") == "Adam's Checking"
    rows = repo.list_txns(conn, show_reconciled=True)
    assert len(rows) == 1
    assert rows[0]["payee"] == "Opening balance"
    assert rows[0]["status"] == "reconciled"
    assert rows[0]["is_opening"] == 1


def test_text_fields_are_stripped_and_blank_becomes_null(conn):
    tid = add(conn, "2026-10-05", "  Goodwill \t", -800, number="  ", note=" thrift  ")
    t = repo.get_txn(conn, tid)
    assert t["payee"] == "Goodwill"
    assert t["number"] is None
    assert t["note"] == "thrift"


def test_blank_payee_rejected(conn):
    with pytest.raises(ValueError):
        add(conn, "2026-10-05", "   ", -800)


def test_totals(ledger):
    balance, verified = repo.totals(ledger)
    assert balance == 100000 + 200000 - 4531 - 999
    assert verified == 100000 + 200000


def test_list_newest_first_with_full_ledger_running_balance(ledger):
    rows = repo.list_txns(ledger, show_reconciled=True)
    assert [r["payee"] for r in rows] == ["SiriusXM", "Fozzys", "Paycheck", "Opening balance"]
    assert [r["running_cents"] for r in rows] == [294470, 295469, 300000, 100000]


def test_reconciled_hidden_by_default(ledger):
    assert "Opening balance" not in [r["payee"] for r in repo.list_txns(ledger)]


def test_same_date_orders_by_id(conn):
    a = add(conn, "2026-10-05", "First", -100)
    b = add(conn, "2026-10-05", "Second", -200)
    rows = repo.list_txns(conn)
    assert [r["id"] for r in rows] == [b, a]
    assert rows[0]["running_cents"] == -300


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("all", {"SiriusXM", "Fozzys", "Paycheck"}),
        ("payments", {"SiriusXM", "Fozzys"}),
        ("deposits", {"Paycheck"}),
        ("unverified", {"SiriusXM", "Fozzys"}),
    ],
)
def test_kind_filter(ledger, kind, expected):
    assert {r["payee"] for r in repo.list_txns(ledger, kind=kind)} == expected


def test_payee_search_is_case_insensitive_substring(ledger):
    assert [r["payee"] for r in repo.list_txns(ledger, q="sirius")] == ["SiriusXM"]


def test_filtered_rows_keep_full_ledger_running_balance(ledger):
    [row] = repo.list_txns(ledger, q="fozzy")
    assert row["running_cents"] == 295469


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("9.99", {"SiriusXM"}),
        ("$45.31", {"Fozzys"}),
        ("2000", {"Paycheck"}),
        ("5-50", {"SiriusXM", "Fozzys"}),
        ("10 - 50", {"Fozzys"}),
        ("1000-", {"Paycheck"}),
        # Partial amounts: whole dollars match that dollar, one cents digit narrows to ten cents.
        ("45", {"Fozzys"}),
        ("45.", {"Fozzys"}),
        ("45.3", {"Fozzys"}),
        ("45.4", set()),
        ("9", {"SiriusXM"}),
        ("9.9", {"SiriusXM"}),
        ("-9", {"SiriusXM"}),
    ],
)
def test_amount_filter(ledger, amount, expected):
    assert {r["payee"] for r in repo.list_txns(ledger, amount=amount)} == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("20.08", (2008, 2008)),
        ("20.0", (2000, 2009)),
        ("20", (2000, 2099)),
        ("20.", (2000, 2099)),
        ("$1,234.5", (123450, 123459)),
        (".5", (50, 59)),
        ("10-50", (1000, 5099)),
        ("10.5-20.25", (1050, 2025)),
        ("100-", (10000, None)),
        ("-20", (None, 2099)),
    ],
)
def test_parse_amount_filter(text, expected):
    assert repo.parse_amount_filter(text) == expected


def test_amount_and_payee_combined(ledger):
    assert repo.list_txns(ledger, q="fozz", amount="9.99") == []


def test_parse_amount_filter_invalid():
    with pytest.raises(ValueError):
        repo.parse_amount_filter("abc")


def test_toggle_verified(ledger):
    [row] = repo.list_txns(ledger, q="Sirius")
    assert repo.toggle_verified(ledger, row["id"]) == "verified"
    assert repo.toggle_verified(ledger, row["id"]) == "unverified"


def test_toggle_does_not_touch_reconciled(ledger):
    opening = repo.list_txns(ledger, show_reconciled=True)[-1]
    assert repo.toggle_verified(ledger, opening["id"]) == "reconciled"


def test_update_keeps_reconciled_status(ledger):
    opening = repo.list_txns(ledger, show_reconciled=True)[-1]
    repo.update_txn(
        ledger,
        opening["id"],
        TxnInput(date="2026-10-01", payee="Opening balance", amount_cents=99000, verified=True),
    )
    assert repo.get_txn(ledger, opening["id"])["status"] == "reconciled"


def test_update_and_delete(ledger):
    [row] = repo.list_txns(ledger, q="Sirius")
    repo.update_txn(
        ledger, row["id"], TxnInput(date="2026-10-11", payee="Sirius XM ", amount_cents=-1099, verified=True)
    )
    t = repo.get_txn(ledger, row["id"])
    assert (t["payee"], t["amount_cents"], t["status"]) == ("Sirius XM", -1099, "verified")
    repo.delete_txn(ledger, row["id"])
    assert repo.get_txn(ledger, row["id"]) is None


def test_suggest_payees_dedupes_case_and_ranks_by_use(conn):
    add(conn, "2026-09-01", "Goodwill", -100)
    add(conn, "2026-09-02", "goodwill", -100)
    add(conn, "2026-09-03", "Gas Station", -100)
    add(conn, "2026-09-04", "Grocery", -100)
    add(conn, "2026-09-05", "Grocery", -100)
    add(conn, "2026-09-06", "Grocery", -100)
    add(conn, "2026-09-07", "Pharmacy", -100)
    assert repo.suggest_payees(conn, "g") == ["Grocery", "goodwill", "Gas Station"]
    assert repo.suggest_payees(conn, "oo") == ["goodwill"]
    assert "Pharmacy" in repo.suggest_payees(conn, "")


def test_suggest_payees_excludes_opening(ledger):
    assert "Opening balance" not in repo.suggest_payees(ledger, "op")


# --- Statement reconciliation ---


def test_reconcile_state(ledger):
    state = repo.reconcile_state(ledger, "2026-10-06", 295469)
    # Candidates: non-reconciled items dated on/before end date.
    assert {r["payee"] for r in state.candidates} == {"Fozzys", "Paycheck"}
    # Cleared: reconciled opening + verified paycheck.
    assert state.cleared_cents == 300000
    assert state.difference_cents == 295469 - 300000
    assert not state.balanced


def test_finish_statement(ledger):
    fozzys = repo.list_txns(ledger, q="Fozzys")[0]
    repo.toggle_verified(ledger, fozzys["id"])
    state = repo.reconcile_state(ledger, "2026-10-06", 295469)
    assert state.balanced
    sid = repo.finish_statement(ledger, "2026-10-06", 295469)
    assert repo.get_txn(ledger, fozzys["id"])["status"] == "reconciled"
    assert repo.get_txn(ledger, fozzys["id"])["statement_id"] == sid
    # Unverified SiriusXM after the end date is untouched.
    assert [r["payee"] for r in repo.list_txns(ledger)] == ["SiriusXM"]
    [stmt] = repo.list_statements(ledger)
    assert stmt["ending_balance_cents"] == 295469
    assert stmt["item_count"] == 2


def test_finish_statement_refuses_when_unbalanced(ledger):
    with pytest.raises(ValueError):
        repo.finish_statement(ledger, "2026-10-06", 1)
    assert repo.list_statements(ledger) == []


def test_verified_after_end_date_not_cleared(ledger):
    [s] = repo.list_txns(ledger, q="Sirius")
    repo.toggle_verified(ledger, s["id"])
    assert repo.reconcile_state(ledger, "2026-10-06", 0).cleared_cents == 300000
