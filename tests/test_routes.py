import re
from datetime import date, timedelta

import pytest

from app import repo
from app.routes import register, spending

HX = {"HX-Request": "true"}


@pytest.fixture
def ready(client):
    r = client.post("/setup", data={"account_name": " Adam's Checking ", "opening": "1,000.00", "date": "2026-10-01"},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


def conn_of(client):
    return client.app.state.conn


def test_first_run_redirects_to_setup(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert "Opening balance" in client.get("/setup").text


def test_setup_validation_error(client):
    r = client.post("/setup", data={"account_name": "", "opening": "lots", "date": "2026-10-01"})
    assert r.status_code == 200
    assert "Enter your current balance" in r.text


def test_register_shows_account_and_totals(ready):
    r = ready.get("/")
    assert r.status_code == 200
    assert "Adam&#39;s Checking" in r.text
    assert "$1,000.00" in r.text


def test_create_txn_strips_and_triggers_refresh(ready):
    r = ready.post("/txn", data={"sign": "-", "amount": "9.99", "payee": "  SiriusXM  ", "date": "2026-10-23",
                                 "number": "", "note": ""}, headers=HX)
    assert r.status_code == 200
    assert "ledger-changed" in r.headers["HX-Trigger"]
    [row] = repo.list_txns(conn_of(ready), q="Sirius")
    assert row["payee"] == "SiriusXM"
    assert row["amount_cents"] == -999
    assert row["number"] is None


def test_create_txn_validation_rerenders_form(ready):
    r = ready.post("/txn", data={"sign": "-", "amount": "", "payee": " ", "date": "2026-10-23"}, headers=HX)
    assert r.status_code == 200
    assert "HX-Trigger" not in r.headers
    assert "Enter an amount" in r.text
    assert "Who was it paid" in r.text


def test_deposit_sign(ready):
    ready.post("/txn", data={"sign": "+", "amount": "50", "payee": "Refund", "date": "2026-10-05"}, headers=HX)
    assert repo.list_txns(conn_of(ready), q="Refund")[0]["amount_cents"] == 5000


def test_edit_form_and_update(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Goodwill")[0]["id"]
    r = ready.get(f"/txn/{tid}/edit", headers=HX)
    assert 'value="8.00"' in r.text and 'aria-label="Save"' in r.text
    assert 'name="verified"' not in r.text
    ready.post(f"/txn/{tid}", data={"sign": "-", "amount": "9", "payee": "Goodwill", "date": "2026-10-17"},
               headers=HX)
    t = repo.get_txn(conn_of(ready), tid)
    assert t["amount_cents"] == -900 and t["status"] == "unverified"


def test_new_from_reconcile_is_dated_and_ticked(ready):
    r = ready.get("/txn/new?date=2026-10-31&verified=1", headers=HX)
    assert 'value="2026-10-31"' in r.text and 'name="verified" value="1"' in r.text
    ready.post("/txn", data={"sign": "-", "amount": "5", "payee": "Bank fee", "date": "2026-10-31",
                             "verified": "1"}, headers=HX)
    assert repo.list_txns(conn_of(ready), q="Bank fee")[0]["status"] == "verified"


def test_reconciled_edit_shows_lock(ready):
    opening = repo.list_txns(conn_of(ready), show_reconciled=True)[-1]
    r = ready.get(f"/txn/{opening['id']}/edit", headers=HX)
    assert "Edit anyway" in r.text
    assert "Delete transaction" not in r.text  # opening balance can't be deleted


def test_delete(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Goodwill")[0]["id"]
    assert ready.post(f"/txn/{tid}/delete", headers=HX).status_code == 200
    assert repo.get_txn(conn_of(ready), tid) is None


def test_register_has_no_verify_controls(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    r = ready.get("/")
    assert "/toggle" not in r.text and "Unverified" not in r.text
    assert "Reconciled" in r.text and "Not yet" in r.text


def test_ledger_partial_filters(ready):
    for payee, amt in [("SiriusXM", "9.99"), ("Fozzys", "45.31")]:
        ready.post("/txn", data={"sign": "-", "amount": amt, "payee": payee, "date": "2026-10-10"}, headers=HX)
    r = ready.get("/?q=fozz", headers={**HX, "HX-Target": "ledger"})
    assert "Fozzys" in r.text and "SiriusXM" not in r.text
    assert "<html" not in r.text
    r = ready.get("/?amount=9.99", headers={**HX, "HX-Target": "ledger"})
    assert "SiriusXM" in r.text and "Fozzys" not in r.text
    r = ready.get("/?amount=abc")
    assert "isn&#39;t an amount" in r.text


def _cursor(html):
    m = re.search(r'"before": "([^"]+)"', html)
    return m and m.group(1)


@pytest.fixture
def paged(ready, monkeypatch):
    monkeypatch.setattr(register, "PAGE_SIZE", 2)
    conn = conn_of(ready)
    today = date.today()
    # Two future rows, then three past ones (newest first).
    for offset, payee in [(2, "Future A"), (1, "Future B"), (-1, "Past C"), (-2, "Past D"), (-3, "Past E")]:
        repo.create_txn(conn, repo.TxnInput((today + timedelta(days=offset)).isoformat(), payee, -100))
    return ready


def test_ledger_pages_load_through_the_placeholder(paged):
    page = paged.get("/").text
    assert "Future A" in page and "Future B" in page and "Past C" not in page
    assert 'class="today-rule"' not in page
    cursor = _cursor(page)
    r = paged.get(f"/?before={cursor}", headers=HX)
    assert "<html" not in r.text and 'id="totals"' not in r.text
    # The page break fell between the future rows and the past ones: the rule comes first.
    assert r.text.index('class="today-rule"') < r.text.index("Past C")
    assert "Past D" in r.text and "Future B" not in r.text
    last = paged.get(f"/?before={_cursor(r.text)}", headers=HX).text
    assert "Past E" in last and "ledger-more" not in last and "today-rule" not in last


def test_ledger_refresh_keeps_loaded_rows(paged):
    r = paged.get("/?rows=4", headers={**HX, "HX-Target": "ledger"})
    assert "Past D" in r.text and "Past E" not in r.text and "ledger-more" in r.text
    r = paged.get("/?rows=0", headers={**HX, "HX-Target": "ledger"})
    assert "Future B" in r.text and "Past C" not in r.text
    # Next-page requests also carry `rows` (htmx merges hx-vals from ancestors): still one page.
    cursor = _cursor(r.text)
    r = paged.get(f"/?before={cursor}&rows=4", headers=HX)
    assert "Past D" in r.text and "Past E" not in r.text


def test_ledger_rejects_a_bad_cursor(paged):
    assert paged.get("/?before=yesterday", headers=HX).status_code == 400


def test_show_reconciled_counts_as_filtering(ready):
    assert "filterBar(false)" in ready.get("/").text
    assert "filterBar(true)" in ready.get("/?show_reconciled=true").text


def test_payees_endpoint(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    assert ready.get("/payees?q=good").json() == ["Goodwill"]


def test_reconcile_flow(ready):
    ready.post("/txn", data={"sign": "-", "amount": "100", "payee": "Rent", "date": "2026-10-05"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Rent")[0]["id"]
    r = ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "900"}, follow_redirects=False)
    assert r.status_code == 303
    r = ready.get("/reconcile")
    assert "Rent" in r.text and "Difference must be $0.00" in r.text and 'value="900.00"' in r.text
    assert "Continue reconciling" in ready.get("/").text
    r = ready.post(f"/reconcile/{tid}/toggle", headers=HX)
    assert "is-balanced" in r.text and "Finish statement" in r.text
    r = ready.post("/reconcile/finish", follow_redirects=False)
    assert r.status_code == 303 and "done=" in r.headers["location"]
    assert repo.get_txn(conn_of(ready), tid)["status"] == "reconciled"
    assert repo.get_draft(conn_of(ready)) is None
    assert "Statement reconciled" in ready.get(r.headers["location"]).text
    assert "Reconciled through Oct 31, 2026" in ready.get("/").text


def test_reconcile_start_validation(ready):
    r = ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "lots"})
    assert r.status_code == 200 and "ending balance" in r.text
    assert repo.get_draft(conn_of(ready)) is None


def test_reconcile_body_partial(ready):
    ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "900"})
    r = ready.get("/reconcile", headers={**HX, "HX-Target": "rec-body"})
    assert 'id="rec-summary"' in r.text and "<html" not in r.text


def test_reconcile_finish_unbalanced_stays(ready):
    ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "5"})
    r = ready.post("/reconcile/finish", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/reconcile"
    assert repo.list_statements(conn_of(ready)) == []


def test_reconcile_toggle_needs_draft(ready):
    opening = repo.list_txns(conn_of(ready), show_reconciled=True)[-1]
    assert ready.post(f"/reconcile/{opening['id']}/toggle", headers=HX).status_code == 400


def test_reconcile_discard(ready):
    ready.post("/txn", data={"sign": "-", "amount": "100", "payee": "Rent", "date": "2026-10-05"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Rent")[0]["id"]
    ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "900"})
    ready.post(f"/reconcile/{tid}/toggle", headers=HX)
    r = ready.post("/reconcile/discard", follow_redirects=False)
    assert r.status_code == 303
    assert repo.get_draft(conn_of(ready)) is None
    assert repo.get_txn(conn_of(ready), tid)["status"] == "unverified"


def test_export_csv(ready):
    ready.post("/txn", data={"sign": "-", "amount": "9.99", "payee": "SiriusXM", "date": "2026-10-23"}, headers=HX)
    r = ready.get("/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "date,payee,amount,number,note,status,running_balance"
    assert lines[-1] == "2026-10-23,SiriusXM,-9.99,,,unverified,990.01"


def test_pwa_endpoints(ready):
    m = ready.get("/manifest.webmanifest")
    assert m.json()["display"] == "standalone"
    assert all("?v=" in i["src"] for i in m.json()["icons"])
    sw = ready.get("/sw.js")
    assert sw.headers["cache-control"] == "no-cache"
    assert ready.app.state.assets.version in sw.text
    assert ready.get("/offline").status_code == 200
    assert ready.get("/favicon.ico").status_code == 200


# --- Spending ---


def pay(client, on: date, payee: str, cents: int):
    repo.create_txn(conn_of(client), repo.TxnInput(on.isoformat(), payee, -cents))


@pytest.mark.parametrize("period, today, start", [
    ("month", date(2026, 10, 21), "2026-10-01"),
    ("3m", date(2026, 10, 21), "2026-08-01"),
    ("3m", date(2026, 2, 5), "2025-12-01"),
    ("year", date(2026, 10, 21), "2026-01-01"),
])
def test_period_range(period, today, start):
    assert spending.period_range(period, today) == (start, today.isoformat())


def test_spending_page(ready):
    today = date.today()
    pay(ready, today, "Rent", 165000)
    pay(ready, today, "Kroger", 2000)
    pay(ready, today, "kroger", 3000)
    r = ready.get("/spending")
    assert r.status_code == 200
    text = r.text
    assert "$1,700.00" in text  # paid out
    assert "2 payees" in text and "3 payments" in text
    assert text.index("Rent") < text.index("kroger")  # biggest first, latest spelling
    assert "/?q=kroger&amp;kind=payments&amp;show_reconciled=true" in text
    assert "Opening balance" not in text
    assert "Everything else" not in text


def test_spending_folds_the_tail(ready):
    today = date.today()
    for i in range(12):
        pay(ready, today, f"Payee {i:02d}", 10000 - i)
    text = ready.get("/spending").text
    assert "Everything else" in text and "spend-sub\">2 payees<" in text
    # One payee past the top ten is shown as itself, not folded.
    conn = conn_of(ready)
    conn.execute("DELETE FROM transactions WHERE payee = 'Payee 11'")
    assert "Everything else" not in ready.get("/spending").text


def test_spending_period_param(ready):
    today = date.today()
    pay(ready, today, "Today shop", 500)
    pay(ready, date(today.year, 1, 1), "New year shop", 700)
    pay(ready, today + timedelta(days=3), "Future shop", 900)
    month = ready.get("/spending").text
    year = ready.get("/spending?period=year").text
    assert "Today shop" in month and "Future shop" not in month
    assert "New year shop" in year and "Future shop" not in year
    if today.month != 1:
        assert "New year shop" not in month
    assert ready.get("/spending?period=bogus").text == month


def test_spending_body_partial_and_empty_state(ready):
    r = ready.get("/spending?period=3m", headers={**HX, "HX-Target": "spending-body"})
    assert "<html" not in r.text
    assert "No payments" in r.text


def test_spending_needs_setup(client):
    r = client.get("/spending", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_register_menu_links_to_spending(ready):
    assert 'href="/spending"' in ready.get("/").text
