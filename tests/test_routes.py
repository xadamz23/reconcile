import pytest

from app import repo

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
    assert 'value="8.00"' in r.text and "Save changes" in r.text
    ready.post(f"/txn/{tid}", data={"sign": "-", "amount": "9", "payee": "Goodwill", "date": "2026-10-17",
                                    "verified": "1"}, headers=HX)
    t = repo.get_txn(conn_of(ready), tid)
    assert t["amount_cents"] == -900 and t["status"] == "verified"


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


def test_toggle_returns_row_and_oob_totals(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Goodwill")[0]["id"]
    r = ready.post(f"/txn/{tid}/toggle", headers=HX)
    assert f'id="txn-{tid}"' in r.text and "is-verified" in r.text
    assert 'id="totals"' in r.text and 'hx-swap-oob="true"' in r.text
    assert "$992.00" in r.text


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


def test_payees_endpoint(ready):
    ready.post("/txn", data={"sign": "-", "amount": "8", "payee": "Goodwill", "date": "2026-10-17"}, headers=HX)
    assert ready.get("/payees?q=good").json() == ["Goodwill"]


def test_reconcile_flow(ready):
    ready.post("/txn", data={"sign": "-", "amount": "100", "payee": "Rent", "date": "2026-10-05"}, headers=HX)
    tid = repo.list_txns(conn_of(ready), q="Rent")[0]["id"]
    r = ready.get("/reconcile?end_date=2026-10-31&balance=900")
    assert "Rent" in r.text and "Difference must be $0.00" in r.text
    r = ready.post(f"/reconcile/{tid}/toggle", data={"end_date": "2026-10-31", "balance": "900"}, headers=HX)
    assert "is-balanced" in r.text and "Finish statement" in r.text
    r = ready.post("/reconcile/finish", data={"end_date": "2026-10-31", "balance": "900"}, follow_redirects=False)
    assert r.status_code == 303 and "done=" in r.headers["location"]
    assert repo.get_txn(conn_of(ready), tid)["status"] == "reconciled"
    assert "Statement reconciled" in ready.get(r.headers["location"]).text


def test_reconcile_finish_unbalanced_redirects_back(ready):
    r = ready.post("/reconcile/finish", data={"end_date": "2026-10-31", "balance": "5"}, follow_redirects=False)
    assert r.status_code == 303 and "balance=5" in r.headers["location"]
    assert repo.list_statements(conn_of(ready)) == []


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
