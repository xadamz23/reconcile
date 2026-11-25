"""Folding the Bills page's sections. Saved in the database, so every device matches."""

import re
from datetime import date

import pytest

HX = {"HX-Request": "true"}
THIS_MONTH = date.today().strftime("%Y-%m")


@pytest.fixture
def ready(client):
    client.post("/setup", data={"account_name": "Checking", "opening": "1,000.00", "date": "2026-10-01"})
    return client


def add_bill(client, payee, amount, day):
    client.post("/bills", data={"payee": payee, "amount": amount, "day": day}, headers=HX)
    return client.app.state.conn.execute("SELECT id FROM bills ORDER BY id DESC").fetchone()["id"]


@pytest.fixture
def billed(ready):
    """One bill still to add and one done, so every section shows."""
    add_bill(ready, "Rent", "1250.00", "1")
    water = add_bill(ready, "Water", "62.40", "5")
    ready.post(f"/bills/{water}/add", data={"seq": 1, "month": THIS_MONTH}, headers=HX)
    return ready


def folds(text: str) -> set[str]:
    """The folded sections. Each is drawn twice (phone rows and desktop cards), and both
    must agree."""
    seen: dict[str, list[bool]] = {}
    for cls, key in re.findall(r'<section class="(sec\b[^"]*)" data-sec="(\w+)"', text):
        seen.setdefault(key, []).append("is-collapsed" in cls.split())
    assert seen, "no foldable sections"
    for key, states in seen.items():
        assert len(states) == 2 and len(set(states)) == 1, (key, states)
    return {key for key, states in seen.items() if states[0]}


def test_done_and_next_month_start_folded(billed):
    text = billed.get("/bills").text
    assert folds(text) == {"done", "next"}
    assert re.search(r'data-fold="done"[^>]*aria-expanded="false"', text)
    assert re.search(r'data-fold="open"[^>]*aria-expanded="true"', text)


def test_folding_is_saved(billed):
    assert billed.post("/bills/fold", data={"key": "open", "folded": "1"}).status_code == 204
    assert billed.post("/bills/fold", data={"key": "done", "folded": "0"}).status_code == 204
    assert folds(billed.get("/bills").text) == {"open", "next"}


def test_unfolding_everything_is_remembered(billed):
    for key in ("done", "next"):
        billed.post("/bills/fold", data={"key": key, "folded": "0"})
    assert folds(billed.get("/bills").text) == set()


def test_fold_rejects_an_unknown_section(billed):
    assert billed.post("/bills/fold", data={"key": "bogus", "folded": "1"}).status_code == 400


def test_refreshing_the_list_keeps_the_folds(billed):
    billed.post("/bills/fold", data={"key": "open", "folded": "1"})
    r = billed.get("/bills", headers={**HX, "HX-Target": "bills-body"})
    assert folds(r.text) == {"open", "done", "next"}


def test_a_folded_section_names_its_bills_and_total(billed):
    text = billed.get("/bills").text
    assert re.search(r'<span class="sec-names">Water</span><span class="sec-total num">\$62\.40</span>', text)
