import json
import re
import threading
from datetime import date, timedelta

import pytest

from app import repo
from app.routes import overview, register, spending

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
    r = ready.get("/register")
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


def test_huge_amounts_are_form_errors_not_crashes(ready):
    r = ready.post("/txn", data={"sign": "-", "amount": "9" * 20, "payee": "X", "date": "2026-10-23"}, headers=HX)
    assert r.status_code == 200
    assert "HX-Trigger" not in r.headers
    assert "Enter an amount" in r.text
    r = ready.get("/register", params={"amount": "999999999999999999999.5"}, headers={**HX, "HX-Target": "ledger"})
    assert r.status_code == 200
    assert "isn&#39;t an amount" in r.text


def test_out_of_range_numbers_are_bad_requests(ready):
    assert ready.get("/txn/99999999999999999999/edit").status_code == 400
    assert ready.get("/register", params={"rows": "9" * 20}).status_code == 400
    assert ready.get("/register", params={"before": "2026-10-01:" + "9" * 20}).status_code == 400


def test_requests_take_turns_on_the_shared_connection(ready):
    lock = ready.app.state.db_lock
    result = {}
    lock.acquire()
    try:
        t = threading.Thread(target=lambda: result.update(r=ready.get("/register")))
        t.start()
        t.join(0.3)
        assert t.is_alive(), "request ran while another held the DB lock"
    finally:
        lock.release()
    t.join(5)
    assert result["r"].status_code == 200


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
    r = ready.get("/register")
    assert "/toggle" not in r.text and "Unverified" not in r.text
    assert "Reconciled" in r.text and "Not yet" in r.text


def test_ledger_partial_filters(ready):
    for payee, amt in [("SiriusXM", "9.99"), ("Fozzys", "45.31")]:
        ready.post("/txn", data={"sign": "-", "amount": amt, "payee": payee, "date": "2026-10-10"}, headers=HX)
    r = ready.get("/register?q=fozz", headers={**HX, "HX-Target": "ledger"})
    assert "Fozzys" in r.text and "SiriusXM" not in r.text
    assert "<html" not in r.text
    r = ready.get("/register?amount=9.99", headers={**HX, "HX-Target": "ledger"})
    assert "SiriusXM" in r.text and "Fozzys" not in r.text
    r = ready.get("/register?amount=abc")
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
    page = paged.get("/register").text
    assert "Future A" in page and "Future B" in page and "Past C" not in page
    assert 'class="today-rule"' not in page
    cursor = _cursor(page)
    r = paged.get(f"/register?before={cursor}", headers=HX)
    assert "<html" not in r.text and 'id="totals"' not in r.text
    # The page break fell between the future rows and the past ones: the rule comes first.
    assert r.text.index('class="today-rule"') < r.text.index("Past C")
    assert "Past D" in r.text and "Future B" not in r.text
    last = paged.get(f"/register?before={_cursor(r.text)}", headers=HX).text
    assert "Past E" in last and "ledger-more" not in last and "today-rule" not in last


def test_ledger_refresh_keeps_loaded_rows(paged):
    r = paged.get("/register?rows=4", headers={**HX, "HX-Target": "ledger"})
    assert "Past D" in r.text and "Past E" not in r.text and "ledger-more" in r.text
    r = paged.get("/register?rows=0", headers={**HX, "HX-Target": "ledger"})
    assert "Future B" in r.text and "Past C" not in r.text
    # Next-page requests also carry `rows` (htmx merges hx-vals from ancestors): still one page.
    cursor = _cursor(r.text)
    r = paged.get(f"/register?before={cursor}&rows=4", headers=HX)
    assert "Past D" in r.text and "Past E" not in r.text


def test_ledger_rejects_a_bad_cursor(paged):
    assert paged.get("/register?before=yesterday", headers=HX).status_code == 400


def test_reconciled_stays_off_until_asked_for(ready):
    assert 'name="show_reconciled" value="true" >' in ready.get("/register").text
    assert 'name="show_reconciled" value="true" checked' in ready.get("/register?q=x&show_reconciled=true").text


def test_show_reconciled_counts_as_filtering(ready):
    assert "filterBar(false)" in ready.get("/register").text
    assert "filterBar(true)" in ready.get("/register?show_reconciled=true").text


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
    assert "Continue reconciling" in ready.get("/register").text
    r = ready.post(f"/reconcile/{tid}/toggle", headers=HX)
    assert "is-balanced" in r.text and "Finish statement" in r.text
    r = ready.post("/reconcile/finish", follow_redirects=False)
    assert r.status_code == 303 and "done=" in r.headers["location"]
    assert repo.get_txn(conn_of(ready), tid)["status"] == "reconciled"
    assert repo.get_draft(conn_of(ready)) is None
    assert "Statement reconciled" in ready.get(r.headers["location"]).text
    assert "Reconciled through Oct 31, 2026" in ready.get("/register").text


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
    assert "/register?q=kroger&amp;kind=payments&amp;show_reconciled=true" in text
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
    assert 'href="/spending"' in ready.get("/register").text


# --- Mobile tab bar ---

TABS = ['href="/register"', "Search", 'href="/"', 'href="/reconcile"']


def tabbar(text):
    start = text.index('class="tabbar')
    return text[start:text.index("</nav>", start)]


def test_register_tab_bar(ready):
    text = ready.get("/register").text
    bar = tabbar(text)
    assert all(t in bar for t in TABS)
    assert 'hx-get="/txn/new"' in bar and "data-new-txn" in bar
    assert ":aria-current=" in bar  # Register is current unless search is open
    assert 'class="fab"' not in text
    assert 'aria-label="Search and filter"' not in text  # the masthead search icon is gone
    assert 'aria-label="Close search and clear filters"' in text  # the ✕ stays


def test_spending_tab_bar(ready):
    bar = tabbar(ready.get("/spending").text)
    assert all(t in bar for t in TABS)
    assert 'href="/register#search"' in bar and 'href="/register#new"' in bar
    assert 'href="/spending"' not in bar  # Spending is in the menu and on the Overview
    assert "aria-current" not in bar


def test_overview_tab_bar(ready):
    bar = tabbar(ready.get("/").text)
    assert 'href="/" aria-current="page"' in bar
    assert 'href="/register#new"' in bar


def test_reconcile_tab_bar(ready):
    bar = tabbar(ready.get("/reconcile").text)
    assert 'href="/register#new"' in bar  # no reconcile in progress, so no form panel here
    assert 'href="/reconcile" aria-current="page"' in bar
    ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "1000"})
    text = ready.get("/reconcile").text
    bar = tabbar(text)
    assert 'hx-get="/txn/new?date=2026-10-31&amp;verified=1"' in bar and "data-new-txn" in bar
    assert 'class="fab"' not in text


def test_tab_bar_order(ready):
    bar = tabbar(ready.get("/spending").text)
    order = ['href="/reconcile"', 'href="/"', 'href="/register#new"', 'href="/register#search"', 'href="/register"']
    assert [bar.index(t) for t in order] == sorted(bar.index(t) for t in order)


def test_new_form_focuses_through_app_js_not_htmx(ready):
    # htmx focuses [autofocus] mid-slide on phones, which opens the keyboard too early.
    text = ready.get("/txn/new", headers=HX).text
    assert "data-autofocus" in text and " autofocus" not in text


# --- Masthead ---


@pytest.mark.parametrize("path", ["/register", "/reconcile", "/spending"])
def test_every_page_has_the_same_masthead(ready, path):
    text = ready.get(path).text
    assert '<h1 class="account">Adam&#39;s Checking</h1>' in text
    assert 'id="totals"' in text
    assert 'aria-label="Menu"' in text


@pytest.mark.parametrize("path", ["/", "/register", "/reconcile", "/spending", "/bills"])
def test_masthead_shows_today(ready, path, monkeypatch):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 6)

    monkeypatch.setattr("app.routes.deps.date", FakeDate)
    text = ready.get(path).text
    # Under the account name.
    assert re.search(r'<h1 class="account">[^<]*</h1>\s*<time class="today" datetime="2026-11-06">Fri, Nov 6</time>',
                     text)


def test_reconcile_refresh_updates_masthead_totals(ready):
    ready.post("/reconcile/start", data={"end_date": "2026-10-31", "balance": "1000.00"})
    r = ready.get("/reconcile", headers={"HX-Request": "true", "HX-Target": "rec-body"})
    assert 'id="totals" class="totals" hx-swap-oob="true"' in r.text


# --- Bills ---


def _add_bill(client, payee="Water", amount="62.40", day="5"):
    r = client.post("/bills", data={"payee": payee, "amount": amount, "day": day}, headers=HX)
    assert r.status_code == 200 and "ledger-changed" in r.headers["HX-Trigger"]
    return conn_of(client).execute("SELECT id FROM bills ORDER BY id DESC").fetchone()["id"]


def test_bills_is_in_the_menu_not_the_tab_bar(ready):
    text = ready.get("/register").text
    menu = text[text.index('class="menu-pop'):text.index("</header>")]
    assert 'href="/bills"' in menu
    assert 'href="/bills"' not in text[text.index('class="tabbar'):]


def test_bills_page_requires_setup(client):
    r = client.get("/bills", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_bills_empty_state(ready):
    assert "No bills yet" in ready.get("/bills").text


def test_bill_form_reports_errors(ready):
    r = ready.post("/bills", data={"payee": " ", "amount": "0", "day": "32"}, headers=HX)
    assert "HX-Trigger" not in r.headers
    for message in ("Who is the bill paid to?", "Enter the amount, or pick Ask if it varies", "Enter a day from 1 to 31"):
        assert message in r.text
    assert conn_of(ready).execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 0


def test_saving_a_bill_never_adds_a_transaction(ready):
    _add_bill(ready)
    assert conn_of(ready).execute("SELECT COUNT(*) FROM transactions WHERE bill_id IS NOT NULL").fetchone()[0] == 0
    assert "$62.40 left to add" in ready.get("/bills").text


def test_edit_and_delete_bill(ready):
    bill = _add_bill(ready)
    assert 'value="62.40"' in ready.get(f"/bills/{bill}/edit", headers=HX).text
    ready.post(f"/bills/{bill}", data={"payee": "City Water", "amount": "70.00", "day": "6"}, headers=HX)
    assert "City Water" in ready.get("/bills").text
    ready.post(f"/bills/{bill}/delete", headers=HX)
    assert "City Water" not in ready.get("/bills").text
    assert ready.get(f"/bills/{bill}/edit", headers=HX).status_code == 404


def test_tick_adds_to_register_updates_balance_and_undo_removes(ready):
    bill = _add_bill(ready)
    before = repo.balance(conn_of(ready))
    r = ready.post(f"/bills/{bill}/add", headers=HX)
    assert repo.balance(conn_of(ready)) == before - 6240
    assert "In register" in r.text and "Undo" in r.text and 'id="totals"' in r.text
    # A second tap (a stale page) doesn't add it twice, or offer an Undo.
    r = ready.post(f"/bills/{bill}/add", headers=HX)
    assert repo.balance(conn_of(ready)) == before - 6240 and "Undo" not in r.text
    r = ready.post(f"/bills/{bill}/remove", headers=HX)
    assert repo.balance(conn_of(ready)) == before and "In register" not in r.text


def test_ticked_on_a_reconcile_cant_be_taken_back_out(ready):
    bill = _add_bill(ready)
    ready.post(f"/bills/{bill}/add", headers=HX)
    txn_id = conn_of(ready).execute("SELECT id FROM transactions WHERE bill_id = ?", (bill,)).fetchone()["id"]
    repo.toggle_verified(conn_of(ready), txn_id)
    text = ready.get("/bills").text
    assert f"/bills/{bill}/remove" not in text
    ready.post(f"/bills/{bill}/remove", headers=HX)
    assert repo.get_txn(conn_of(ready), txn_id) is not None


def _add_daycare(client):
    data = {"payee": "Daycare", "amount": "600.00", "day": "", "times": "2", "day2": "", "mode": "ask"}
    r = client.post("/bills", data=data, headers=HX)
    assert r.status_code == 200 and "ledger-changed" in r.headers["HX-Trigger"]
    return conn_of(client).execute("SELECT id FROM bills ORDER BY id DESC").fetchone()["id"]


def test_bill_form_needs_both_days_for_twice_a_month(ready):
    r = ready.post("/bills", data={"payee": "Daycare", "amount": "600", "day": "1", "times": "2", "day2": "", "mode": "now"},
                   headers=HX)
    assert "HX-Trigger" not in r.headers and "Enter a day from 1 to 31" in r.text


def test_bill_that_asks_shows_two_rows_and_opens_a_sheet(ready):
    bill = _add_daycare(ready)
    text = ready.get("/bills").text
    assert "1st of 2" in text and "2nd of 2" in text and "You pick the date" in text
    assert f'hx-get="/bills/{bill}/add?seq=2"' in text
    repo.create_txn(conn_of(ready), repo.TxnInput("2026-10-20", "Daycare", -60000, number="1042"))
    sheet = ready.get(f"/bills/{bill}/add?seq=2", headers=HX).text
    assert "Use next check, 1043" in sheet and 'value="600.00"' in sheet and "2nd of 2 for" in sheet
    form = ready.get(f"/bills/{bill}/edit", headers=HX).text
    assert re.search(r'value="2" x-model="times" checked', form) and re.search(r'value="ask" x-model="mode" checked', form)
    assert ready.get(f"/bills/{bill}/add?seq=3", headers=HX).status_code == 404


def test_sheet_adds_with_date_check_and_amount(ready):
    bill = _add_daycare(ready)
    on = date.today().replace(day=2).isoformat()
    data = {"seq": "2", "sheet": "1", "amount": "610.00", "date": on, "number": "1043"}
    r = ready.post(f"/bills/{bill}/add", data=data, headers=HX)
    assert "panel-close" in r.headers["HX-Trigger"]
    assert "Added Daycare, −$610.00" in r.text and "Check 1043" in r.text and '"seq": 2' in r.text
    txn = conn_of(ready).execute("SELECT * FROM transactions WHERE bill_id = ?", (bill,)).fetchone()
    assert (txn["date"], txn["amount_cents"], txn["number"], txn["bill_seq"]) == (on, -61000, "1043", 2)
    r = ready.post(f"/bills/{bill}/remove", data={"seq": "2"}, headers=HX)
    assert conn_of(ready).execute("SELECT COUNT(*) FROM transactions WHERE bill_id = ?", (bill,)).fetchone()[0] == 0


def test_sheet_errors_go_back_to_the_panel(ready):
    bill = _add_daycare(ready)
    r = ready.post(f"/bills/{bill}/add", data={"seq": "1", "sheet": "1", "amount": "0", "date": "nope", "number": ""}, headers=HX)
    assert r.headers["HX-Retarget"] == "#panel-body"
    assert "Pick a date" in r.text and "Amount can&#39;t be zero" in r.text
    assert conn_of(ready).execute("SELECT COUNT(*) FROM transactions WHERE bill_id = ?", (bill,)).fetchone()[0] == 0


def _bill(client, **fields):
    data = {"payee": "Card", "amount": "", "day": "10", "times": "1", "day2": "", "mode": "now", "source": "here",
            "sometimes": "", **fields}
    r = client.post("/bills", data=data, headers=HX)
    assert r.status_code == 200 and "ledger-changed" in r.headers["HX-Trigger"], r.text
    return conn_of(client).execute("SELECT id FROM bills ORDER BY id DESC").fetchone()["id"]


def _txn_count(client, bill):
    return conn_of(client).execute("SELECT COUNT(*) FROM transactions WHERE bill_id = ?", (bill,)).fetchone()[0]


def test_another_account_bill_marks_paid_and_adds_nothing(ready):
    bill = _bill(ready, payee="Car insurance", amount="142.00", day="15", source="other")
    _add_bill(ready)  # Water, from this account
    text = ready.get("/bills").text
    assert "Other account" in text and "$62.40 left to add" in text  # its $142 isn't leaving this account
    assert f'hx-post="/bills/{bill}/mark"' in text
    before = repo.balance(conn_of(ready))
    r = ready.post(f"/bills/{bill}/mark", data={"seq": "1", "kind": "paid"}, headers=HX)
    assert "Marked Car insurance paid" in r.text and "Paid " in r.text and f"/bills/{bill}/unmark" in r.text
    assert repo.balance(conn_of(ready)) == before and _txn_count(ready, bill) == 0
    # Paid rows drop to the bottom.
    rows = r.text[r.text.index("card-list"):]
    assert rows.index("Water") < rows.index("Car insurance")
    ready.post(f"/bills/{bill}/unmark", data={"seq": "1"}, headers=HX)
    assert "Paid " not in ready.get("/bills").text
    # It can't be added to the register, or skipped (it's due every month).
    assert ready.post(f"/bills/{bill}/add", data={"seq": "1"}, headers=HX).status_code == 400
    assert ready.post(f"/bills/{bill}/mark", data={"seq": "1", "kind": "skipped"}, headers=HX).status_code == 400


def test_bill_that_varies_and_isnt_due_every_month(ready):
    bill = _bill(ready, payee="Credit card", mode="ask", day="", sometimes="1")
    text = ready.get("/bills").text
    assert "Varies" in text and f'hx-get="/bills/{bill}/add?seq=1"' in text
    sheet = ready.get(f"/bills/{bill}/add?seq=1", headers=HX).text
    assert "amount varies" in sheet and "Nothing due this month" in sheet
    r = ready.post(f"/bills/{bill}/mark", data={"seq": "1", "kind": "skipped", "sheet": "1"}, headers=HX)
    assert "panel-close" in r.headers["HX-Trigger"] and "Nothing due in" in r.text and "1 of 1 done" in r.text
    # Marked nothing due, so it can't be added too.
    data = {"seq": "1", "sheet": "1", "amount": "50.00", "date": date.today().isoformat(), "number": ""}
    ready.post(f"/bills/{bill}/add", data=data, headers=HX)
    assert _txn_count(ready, bill) == 0
    ready.post(f"/bills/{bill}/unmark", data={"seq": "1"}, headers=HX)
    ready.post(f"/bills/{bill}/add", data=data, headers=HX)
    assert _txn_count(ready, bill) == 1
    # Added, so it can't be marked nothing due.
    ready.post(f"/bills/{bill}/mark", data={"seq": "1", "kind": "skipped"}, headers=HX)
    assert "Nothing due in" not in ready.get("/bills").text


def test_sometimes_bill_with_a_fixed_amount_opens_the_sheet_on_its_day(ready):
    bill = _bill(ready, payee="Dental plan", amount="45.00", day="3", sometimes="1")
    sheet = ready.get(f"/bills/{bill}/add?seq=1", headers=HX).text
    assert 'value="45.00"' in sheet and f'value="{date.today():%Y-%m}-03"' in sheet
    assert ready.post(f"/bills/{bill}/add", data={"seq": "1"}, headers=HX).status_code == 400


def test_amount_is_required_only_when_added_right_away(ready):
    r = ready.post("/bills", data={"payee": "Card", "amount": "", "day": "1", "times": "1", "mode": "now",
                                   "source": "here"}, headers=HX)
    assert "Enter the amount, or pick Ask if it varies" in r.text
    _bill(ready, payee="Other", source="other")
    assert repo.get_bill(conn_of(ready), 1)["amount_cents"] is None


def test_deleting_a_bill_drops_its_marks(ready):
    bill = _bill(ready, payee="Gym", amount="39.00", source="other")
    ready.post(f"/bills/{bill}/mark", data={"seq": "1", "kind": "paid"}, headers=HX)
    ready.post(f"/bills/{bill}/delete", headers=HX)
    assert conn_of(ready).execute("SELECT COUNT(*) FROM bill_marks").fetchone()[0] == 0


# --- Overview ---


def test_overview_needs_setup(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_setup_lands_on_the_overview(client):
    r = client.post("/setup", data={"account_name": "", "opening": "10", "date": "2026-10-01"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_old_register_links_redirect(ready):
    r = ready.get("/?q=kroger&show_reconciled=true", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/register?q=kroger&show_reconciled=true"


def test_manifest_opens_phones_on_the_register(ready):
    m = ready.get("/manifest.webmanifest").json()
    assert m["start_url"] == "/register"
    assert m["id"] == "/"  # the identity the app was installed with, when start_url was "/"


def test_overview_masthead_and_navigation(ready):
    text = ready.get("/").text
    assert '<h1 class="account">Adam&#39;s Checking</h1>' in text
    assert 'id="totals"' not in text  # the tiles show them
    assert 'href="/register#new" data-new-txn' in text  # desktop New transaction, and the n key
    menu = text[text.index('class="menu-pop'):text.index("</header>")]
    assert 'href="/register"' in menu and 'href="/"' in menu
    # Every other page's desktop back arrow comes here.
    for path in ("/register", "/reconcile", "/spending", "/bills"):
        assert 'href="/" aria-label="Back to overview"' in ready.get(path).text


def test_overview_free_after_bills(ready):
    conn = conn_of(ready)
    _add_bill(ready, "Water", "62.40", "28")
    rent = _add_bill(ready, "Rent", "500.00", "1")
    repo.add_bill_txn(conn, rent, date.today().strftime("%Y-%m"))
    # Its amount varies, so it can't be counted.
    ready.post("/bills", data={"payee": "Electric", "amount": "", "mode": "ask"}, headers=HX)
    text = ready.get("/").text
    # $1,000.00 opening - $500 rent = $500.00, less $62.40 water still to add.
    assert "$437.60" in text
    assert "Balance less <b>$62.40</b> in 1 bill still to add" in text
    assert "<b>$500.00</b> added · 1 of 3 done" in text
    assert "Electric&#39;s amount varies" in text


def test_overview_tiles(ready):
    repo.create_txn(conn_of(ready), repo.TxnInput(date.today().isoformat(), "Kroger", -2500))
    text = ready.get("/").text
    assert "1 item<" in text and "-$25.00" in text  # not yet cleared
    assert "Not yet</span>" in text  # nothing reconciled
    assert "Time to reconcile" in text
    assert 'href="/register?q=Kroger&amp;kind=payments&amp;show_reconciled=true"' in text  # where the month went


def test_overview_flags_old_checks(ready):
    old = (date.today() - timedelta(days=45)).isoformat()
    repo.create_txn(conn_of(ready), repo.TxnInput(old, "Lawn service", -25000, number="1043"))
    text = ready.get("/").text
    assert "Check 1043 hasn&#39;t cleared" in text
    assert "Lawn service · $250.00 · written 45 days ago" in text


def test_overview_chart_projects_bills(ready):
    _add_bill(ready, "Water", "100.00", "20")
    ctx = overview.overview_context(conn_of(ready), date(2026, 11, 13))
    chart = ctx["chart"]
    points = json.loads(chart["data"])
    assert len(points) == 90 + 17  # 90 days of history, then Nov 14-30
    assert points[89][2:] == ["Nov 13", "$1,000.00", 0]
    assert points[95][2:] == ["Nov 19", "$1,000.00", 1]
    assert points[96][2:] == ["Nov 20", "$900.00", 1]  # Water comes out
    assert chart["end_label"] == "Nov 30 · $900.00"
    assert [m["label"] for m in ctx["flow"]] == ["Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]


def test_overview_chart_on_the_last_day_of_the_month(ready):
    chart = overview.overview_context(conn_of(ready), date(2026, 11, 30))["chart"]
    assert chart["proj"] is None and chart["end"] is None
