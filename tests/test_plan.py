"""Plan: the what's-left worksheet opened from the desktop Bills page."""

import re

import pytest

from app import repo
from app.money import MAX_CENTS
from app.repo import PlanLine


@pytest.fixture
def ready(client):
    client.post("/setup", data={"account_name": "Checking", "opening": "1,997.22", "date": "2026-10-01"})
    return client


def lines_of(client):
    return [dict(r) for r in repo.plan_lines(client.app.state.conn)]


# --- repo ---


def test_plan_starts_empty(conn):
    assert repo.plan_lines(conn) == []


def test_save_plan_keeps_order_and_trims_text(conn):
    repo.save_plan(conn, [
        PlanLine("  To savings ", 145000, ""),
        PlanLine("Gas", 5500, " $50 "),
        PlanLine("ASB CC", None, None),
    ])
    assert [dict(r) for r in repo.plan_lines(conn)] == [
        {"label": "To savings", "amount_cents": 145000, "note": None},
        {"label": "Gas", "amount_cents": 5500, "note": "$50"},
        {"label": "ASB CC", "amount_cents": None, "note": None},
    ]


def test_save_plan_replaces_the_previous_lines(conn):
    repo.save_plan(conn, [PlanLine("Gas", 5500, None), PlanLine("Rent", 125000, None)])
    repo.save_plan(conn, [PlanLine("Rent", None, None)])
    assert [r["label"] for r in repo.plan_lines(conn)] == ["Rent"]


def test_a_new_line_can_be_unnamed(conn):
    # Add line saves before anything is typed into it.
    repo.save_plan(conn, [PlanLine("   ", None, None)])
    assert [dict(r) for r in repo.plan_lines(conn)] == [{"label": "", "amount_cents": None, "note": None}]


@pytest.mark.parametrize("cents", [-1, MAX_CENTS + 1])
def test_plan_line_amount_out_of_range(cents):
    with pytest.raises(ValueError):
        PlanLine("Gas", cents, None)


# --- routes ---


def test_plan_requires_setup(client):
    r = client.get("/plan", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_plan_page_shows_balance_and_lines(ready):
    repo.save_plan(ready.app.state.conn, [PlanLine("Jackson's gas <3", 5500, "$50")])
    r = ready.get("/plan")
    assert r.status_code == 200
    assert "$1,997.22" in r.text
    data = re.search(r'<script id="plan-data" type="application/json">(.*?)</script>', r.text, re.S).group(1)
    assert "<" not in data  # the JSON is escaped for the script tag
    assert '"cents": 5500' in data and '"note": "$50"' in data


def test_saving_the_plan(ready):
    r = ready.post("/plan", json={"lines": [
        {"label": "Gas", "cents": 5500, "note": "$50"},
        {"label": "ASB CC", "cents": None, "note": ""},
    ]})
    assert r.status_code == 204
    assert lines_of(ready) == [
        {"label": "Gas", "amount_cents": 5500, "note": "$50"},
        {"label": "ASB CC", "amount_cents": None, "note": None},
    ]


@pytest.mark.parametrize("line", [
    {"label": "Gas", "cents": -5, "note": ""},
    {"label": "Gas", "cents": MAX_CENTS + 1, "note": ""},
    {"label": "Gas", "cents": 1.5, "note": ""},
    {"label": "x" * 101, "cents": None, "note": ""},
    {"label": "Gas", "cents": None, "note": "x" * 201},
])
def test_saving_a_bad_plan_changes_nothing(ready, line):
    ready.post("/plan", json={"lines": [{"label": "Rent", "cents": 100, "note": ""}]})
    assert ready.post("/plan", json={"lines": [line]}).status_code == 422
    assert [r["label"] for r in lines_of(ready)] == ["Rent"]


def test_plan_has_a_line_limit(ready):
    lines = [{"label": f"L{i}", "cents": None, "note": ""} for i in range(101)]
    assert ready.post("/plan", json={"lines": lines}).status_code == 422


def test_plan_balance_is_the_register_balance(ready):
    ready.post("/txn", data={"sign": "-", "amount": "10.00", "payee": "Gas", "date": "2026-10-02"})
    assert ready.get("/plan/balance").json() == {"cents": 198722}


def test_bills_page_opens_the_plan(ready):
    text = ready.get("/bills").text
    assert re.search(r'<a [^>]*href="/plan"[^>]*data-plan', text)
