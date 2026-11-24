"""Reading statements: local text extraction and redaction, Claude's answer turned into
signed lines, matching against the register, and the Reconcile routes (with a fake
reader, so no test calls Claude). The statements here are made up, shaped like the
user's bank's."""

import pytest

from app import repo, statement_match, statement_text
from app.statement_ai import Line, Read, StatementReadError, to_statement

HX = {"HX-Request": "true"}

# --- A synthetic statement, laid out the way pypdf reads the bank's PDFs ---------------

ACCOUNT = "4400112233"
SAVINGS = "009988776655"
PAGE_1 = f"""Page  1  of  {{n}}
JANE Q SAMPLE
12 BIRCH LANE
SPRINGFIELD IL 62704-1234
Example Bank N.A.
FINANCIAL SUMMARY ACCOUNT# BALANCE
DEPOSIT ACCOUNTS
Everyday Checking {ACCOUNT} $1,000.00
DEPOSIT ACCOUNTS
Everyday Checking #{ACCOUNT}
Beginning Balance 500.00
Plus: Deposits and Other Additions 1,200.00
Minus: Withdrawals and Other Deductions 630.00
Minus: Checks Paid 70.00
ENDING BALANCE ON 03/19/2026 $1,000.00
Deposits and Other Additions
03/05/2026 ACME CORP PAYROLL XXXXXXXX1234HM6 SAMPLE,JANE Q 1,200.00
TOTAL $1,200.00
Withdrawals and Other Deductions
02/23/2026 DDA PUR COFFEE HUT *1111 SPRINGFIELD IL 605001414934 6.25
02/23/2026 DDA PUR COFFEE HUT *1111 SPRINGFIELD IL 605001414935 6.25
02/24/2026 WEB FR DDA TO SAV {SAVINGS} CONFIRMATION# 073311332531
073311332531
200.00
FINANCIAL STATEMENT OF ACCOUNTS
Primary Account: {ACCOUNT}
Statement Activity Period
02/20/2026 to 03/19/2026
Bank: 001
Mail Code: 0S
Member FDIC. Equal Housing Lender.
"""
FORM_PAGE = """NO. $
TOTAL
*  PLEASE USE THIS FORM TO BALANCE YOUR CHECKING ACCOUNT
CHECKS OUTSTANDING - NOT
* Your name and account  number.
"""
CONTINUED = """Page  {page}  of  {n}JANE Q SAMPLE Acct #{account}
Withdrawals and Other Deductions (continued)
{rows}
"""
LAST = """Page  {n}  of  {n}JANE Q SAMPLE Acct #{account}
Withdrawals and Other Deductions (continued)
03/16/2026 VENMO PAYMENT XXXXXXXXX5556 JANE SAMPLE 400.00
03/17/2026 Out of Network ATM 3.50
TOTAL $630.00
Checks Paid
DATE CHECK# AMOUNT
03/09/2026 2253 70.00
TOTAL $70.00
Total Overdraft Fees
Balance Summary
DATE BALANCE
02/20/2026 259.79
Service Chg Waived $4.00
"""


def synthetic(pages_of_rows: int) -> list[str]:
    """A statement whose withdrawals run over `pages_of_rows` continuation pages, with
    each page's text doubled the way the bank's PDFs carry it."""
    n = 3 + pages_of_rows
    pages = [PAGE_1.format(n=n), FORM_PAGE]
    for k in range(pages_of_rows):
        rows = f"03/{10 + k:02d}/2026 DDA PUR GROCER *1111 SPRINGFIELD IL 6050014149{k:02d} 1.00"
        pages.append(CONTINUED.format(page=2 + k, n=n, account=ACCOUNT, rows=rows))
    pages.append(LAST.format(n=n, account=ACCOUNT))
    return [p + p for p in pages]  # every page's text twice, as in the real PDFs


@pytest.mark.parametrize("extra_pages", [0, 1, 3])
def test_extract_finds_the_sections_on_any_number_of_pages(extra_pages):
    text = statement_text.prepare(synthetic(extra_pages))
    assert text.startswith("Statement Activity Period: 02/20/2026 to 03/19/2026")
    assert "Beginning Balance 500.00" in text and "ENDING BALANCE ON 03/19/2026 $1,000.00" in text
    for heading in ("Deposits and Other Additions", "Withdrawals and Other Deductions", "Checks Paid"):
        assert heading in text
    assert text.count("GROCER") == extra_pages
    assert text.count("COFFEE HUT") == 2  # the doubled text layer is read once
    # The balancing form, the balance table and the fee note are gone.
    for gone in ("PLEASE USE THIS FORM", "Balance Summary", "Service Chg", "259.79", "Mail Code"):
        assert gone not in text
    # The wrapped description keeps its amount on the following line.
    assert "CONFIRMATION# [number]\n[number]\n200.00" in text


def test_extract_redacts_names_addresses_and_account_numbers():
    text = statement_text.prepare(synthetic(2))
    for secret in (ACCOUNT, SAVINGS, "XXXXXXXX1234", "XXXXXXXXX5556", "JANE", "SAMPLE", "BIRCH", "62704"):
        assert secret not in text
    assert "[name]" in text and "[number]" in text
    assert "2253 70.00" in text  # check numbers and amounts survive


def test_only_pdfs_are_read():
    from app.statement_ai import _content
    with pytest.raises(StatementReadError, match="isn't a PDF"):
        _content(b"\x89PNG...", "image/png")
    with pytest.raises(StatementReadError, match="isn't a PDF"):
        _content(b"hello", "application/pdf")  # named .pdf but isn't one


def test_extract_gives_up_on_unknown_layouts():
    assert statement_text.prepare(["Some other bank\n01/02/2026 STORE 5.00"]) is None
    assert statement_text.extract(b"not a pdf") is None


# --- Claude's answer to stored lines -----------------------------------------------------

def _read(**kw):
    lines = kw.pop("lines", [
        Line(date="2026-03-05", section="deposit", description="ACME CORP PAYROLL", payee="Payroll",
             amount="1,200.00", check_number=None),
        Line(date="2026-03-09", section="check", description="Check", payee="Check", amount="70.00",
             check_number="02253"),
        Line(date="2026-03-17", section="withdrawal", description="Out of Network ATM", payee="ATM fee",
             amount="3.50", check_number=None),
    ])
    base = dict(period_start="2026-02-20", closing_date="2026-03-19", beginning_balance="500.00",
                deposits_total="1,200.00", withdrawals_total="3.50", checks_total="70.00",
                ending_balance="$1,626.50", lines=lines)
    return Read(**{**base, **kw})


def test_signs_come_from_the_section_not_from_claude():
    st = to_statement(_read(), "march.pdf", "text")
    assert [ln["cents"] for ln in st["lines"]] == [120000, -7000, -350]
    assert st["lines"][1]["number"] == "2253"
    assert st["ending_cents"] == 162650 and st["closing_date"] == "2026-03-19"


def test_unreadable_answers_are_errors():
    with pytest.raises(StatementReadError):
        to_statement(_read(closing_date="March 19"), "x.pdf", "text")
    with pytest.raises(StatementReadError):
        to_statement(_read(ending_balance="lots"), "x.pdf", "text")
    with pytest.raises(StatementReadError):
        to_statement(_read(lines=[]), "x.pdf", "text")


# --- Matching ----------------------------------------------------------------------------

def _line(day, cents, payee="Store", section=None, number=None):
    section = section or ("check" if number else "deposit" if cents > 0 else "withdrawal")
    return {"date": f"2026-03-{day:02d}", "section": section, "description": payee.upper(), "payee": payee,
            "cents": cents, "number": number}


def _txn(tid, day, cents, payee="Store", number=None, status="unverified"):
    return {"id": tid, "date": f"2026-03-{day:02d}", "payee": payee, "amount_cents": cents, "number": number,
            "status": status}


def _statement(lines, **totals):
    sums = {"deposit": 0, "withdrawal": 0, "check": 0}
    for ln in lines:
        sums[ln["section"]] += abs(ln["cents"])
    st = {"lines": lines, "beginning_cents": 0, "deposits_cents": sums["deposit"],
          "withdrawals_cents": sums["withdrawal"], "checks_cents": sums["check"]}
    st["ending_cents"] = st["deposits_cents"] - st["withdrawals_cents"] - st["checks_cents"]
    return {**st, **totals}


def test_checks_match_by_number_before_amount():
    lines = [_line(9, -7000, "Check", number="2253")]
    txns = [_txn(1, 2, -7000, "Piano", number="2252"), _txn(2, 3, -7000, "Piano", number="2253")]
    review = statement_match.match(_statement(lines), txns)
    assert [(i, t["id"]) for i, t in review.matched] == [(0, 2)]
    assert [t["id"] for t in review.unmatched] == [1]


def test_repeats_pair_off_one_to_one_by_nearest_date():
    lines = [_line(23, -625, "Starbucks"), _line(23, -625, "Starbucks"), _line(25, -625, "Starbucks")]
    txns = [_txn(1, 22, -625, "Starbucks"), _txn(2, 22, -625, "Starbucks")]
    review = statement_match.match(_statement(lines), txns)
    assert sorted(t["id"] for _, t in review.matched) == [1, 2]
    assert review.missing == [2]  # the third coffee isn't in the register


def test_same_amount_too_far_apart_doesnt_match():
    review = statement_match.match(_statement([_line(25, -5000)]), [_txn(1, 1, -5000)])
    assert review.matched == [] and review.missing == [0]


def test_a_cent_off_is_flagged_not_matched():
    lines = [_line(11, 162068, "Payroll")]
    txns = [_txn(1, 11, 162066, "Payroll"), _txn(2, 11, -3000, "Gas")]
    review = statement_match.match(_statement(lines), txns)
    assert review.matched == [] and [(i, t["id"]) for i, t in review.differs] == [(0, 1)]
    assert review.missing == [] and [t["id"] for t in review.unmatched] == [2]


def test_near_amounts_with_different_payees_arent_flagged():
    review = statement_match.match(_statement([_line(10, -3785, "Target")]), [_txn(1, 10, -3758, "Dentist")])
    assert review.differs == [] and review.missing == [0]


def test_ready_is_matched_and_not_yet_ticked():
    lines = [_line(1, -100), _line(2, -200)]
    txns = [_txn(1, 1, -100, status="verified"), _txn(2, 2, -200)]
    review = statement_match.match(_statement(lines), txns)
    assert [t["id"] for t in review.ready] == [2] and review.ticked_count == 1


def test_totals_check_names_the_section_that_is_off():
    lines = [_line(1, -100), _line(2, 500)]
    assert statement_match.totals_check(_statement(lines)) == []
    warnings = statement_match.totals_check(_statement(lines, withdrawals_cents=725))
    assert warnings[0] == "Withdrawals add up to $1.00, the statement says $7.25."
    assert "ending balance" in warnings[1]


def test_payee_suggestion_uses_the_registers_spelling():
    payees = ["Casey's", "Starbucks", "Venmo"]
    assert statement_match.suggest_payee("Caseys", payees) == "Casey's"
    assert statement_match.suggest_payee("STARBUCKS", payees) == "Starbucks"
    assert statement_match.suggest_payee("ATM fee", payees) == "ATM fee"


# --- Routes ------------------------------------------------------------------------------

def conn_of(client):
    return client.app.state.conn


@pytest.fixture
def ai(client):
    """A set-up app with the feature on and a fake reader returning `statement`."""
    client.post("/setup", data={"account_name": "Checking", "opening": "1,000.00", "date": "2026-02-01"})
    state = client.app.state
    state.ai_enabled = True
    state.fake = None
    state.calls = []

    def fake(data, media_type, filename):
        state.calls.append((data, media_type, filename))
        if isinstance(state.fake, Exception):
            raise state.fake
        return state.fake

    state.read_statement = fake
    return client


def add(client, day, amount, payee, number=""):
    sign = "+" if amount > 0 else "-"
    client.post("/txn", data={"sign": sign, "amount": f"{abs(amount):.2f}", "payee": payee,
                              "date": f"2026-03-{day:02d}", "number": number}, headers=HX)
    return repo.list_txns(conn_of(client), q=payee)[0]["id"]


def statement_for(lines, closing="2026-03-19", beginning=100000):
    st = _statement(lines, beginning_cents=beginning)
    st["ending_cents"] += beginning
    return {**st, "file": "march.pdf", "source": "text", "period_start": "2026-02-20", "closing_date": closing}


def upload(client, name="march.pdf", body=b"%PDF-1.4 fake", kind="application/pdf"):
    return client.post("/reconcile/statement", files={"file": (name, body, kind)}, follow_redirects=False)


def test_read_statement_is_hidden_without_a_key(client):
    client.post("/setup", data={"account_name": "Checking", "opening": "1,000.00", "date": "2026-02-01"})
    client.app.state.ai_enabled = False
    assert "Read statement" not in client.get("/reconcile").text
    assert upload(client).status_code == 404


def test_upload_starts_the_reconcile_from_the_statement(ai):
    rent = add(ai, 2, -50, "Rent")
    coffee = add(ai, 3, -6.25, "Starbucks")
    ai.app.state.fake = statement_for([_line(2, -5000, "Rent"), _line(4, -625, "Starbucks"), _line(14, -500, "Fee")])
    assert "Read statement" in ai.get("/reconcile").text

    r = upload(ai)
    assert r.status_code == 303 and r.headers["location"] == "/reconcile"
    assert ai.app.state.calls[0][1:] == ("application/pdf", "march.pdf")
    assert repo.get_draft(conn_of(ai)) == ("2026-03-19", 100000 - 5625 - 500)

    page = ai.get("/reconcile").text
    assert "2 of 3 statement lines match" in page and "Tick 2 matched" in page
    assert page.count("From statement") == 2 and "march.pdf · 3 lines read" in page
    assert page.count("chip-done") == 2 and "MONTHLY" not in page
    assert "/txn/new?date=2026-03-14&amp;verified=1&amp;payee=Fee&amp;cents=-500" in page

    r = ai.post("/reconcile/statement/apply", headers=HX)
    assert "Ticked 2 items" in r.text and "All matches ticked" in r.text
    assert {repo.get_txn(conn_of(ai), t)["status"] for t in (rent, coffee)} == {"verified"}

    # Undo unticks them again.
    r = ai.post("/reconcile/statement/undo", data={"ids": f"{rent},{coffee}"}, headers=HX)
    assert "Tick 2 matched" in r.text
    assert repo.get_txn(conn_of(ai), rent)["status"] == "unverified"


def test_reading_doesnt_hold_the_database_lock(ai):
    """Other requests carry on while Claude reads."""
    lock = ai.app.state.db_lock

    def fake(data, media_type, filename):
        assert not lock.locked()
        return statement_for([_line(2, -5000, "Rent")])

    ai.app.state.read_statement = fake
    assert upload(ai).status_code == 303


def test_read_errors_show_on_the_page(ai):
    ai.app.state.fake = StatementReadError("Claude declined to read that file.")
    r = upload(ai)
    assert r.status_code == 200 and "Claude declined to read that file." in r.text
    assert repo.get_draft(conn_of(ai)) is None
    r = upload(ai, body=b"")
    assert "Choose a statement file" in r.text and len(ai.app.state.calls) == 1


def test_fix_sets_the_statement_amount_and_ticks(ai):
    target = add(ai, 9, -37.58, "Target")
    ai.app.state.fake = statement_for([_line(10, -3785, "Target")])
    upload(ai)
    page = ai.get("/reconcile").text
    assert "Amount differs" in page and "Register has Target for $37.58" in page
    r = ai.post("/reconcile/statement/fix", data={"txn": target, "line": 0}, headers=HX)
    assert r.status_code == 200 and "Changed <strong>Target</strong> to $37.85 and ticked it" in r.text
    row = repo.get_txn(conn_of(ai), target)
    assert row["amount_cents"] == -3785 and row["status"] == "verified"
    # Fixing it again is refused: it no longer differs.
    assert ai.post("/reconcile/statement/fix", data={"txn": target, "line": 0}, headers=HX).status_code == 409


def test_finish_and_discard_drop_the_statement(ai):
    rent = add(ai, 2, -50, "Rent")
    ai.app.state.fake = statement_for([_line(2, -5000, "Rent")], beginning=100000)
    upload(ai)
    ai.post("/reconcile/statement/apply", headers=HX)
    r = ai.post("/reconcile/finish", follow_redirects=False)
    assert "done=" in r.headers["location"]
    assert repo.get_statement(conn_of(ai)) is None
    assert repo.get_txn(conn_of(ai), rent)["status"] == "reconciled"

    add(ai, 3, -20, "Gas")
    ai.app.state.fake = statement_for([_line(3, -2000, "Gas")], beginning=95000)
    upload(ai)
    ai.post("/reconcile/discard")
    assert repo.get_statement(conn_of(ai)) is None


def test_totals_warning_shows_in_the_review(ai):
    add(ai, 2, -50, "Rent")
    st = statement_for([_line(2, -5000, "Rent")])
    st["withdrawals_cents"] = 6000
    ai.app.state.fake = st
    upload(ai)
    assert "Withdrawals add up to $50.00, the statement says $60.00." in ai.get("/reconcile").text


def test_new_transaction_form_is_filled_from_a_statement_line(ai):
    r = ai.get("/txn/new?date=2026-03-14&verified=1&payee=ATM+fee&cents=-350&number=", headers=HX)
    assert 'value="3.50"' in r.text and "payeeBox(\"ATM fee\")" in r.text
    assert 'value="2026-03-14"' in r.text and 'value="-" checked' in r.text
    assert 'name="verified" value="1"' in r.text
    r = ai.get("/txn/new?date=2026-03-14&cents=162066&payee=Payroll", headers=HX)
    assert 'value="+" checked' in r.text and 'value="1620.66"' in r.text


def test_cross_site_upload_is_blocked(ai):
    r = ai.post("/reconcile/statement", files={"file": ("x.pdf", b"x", "application/pdf")},
                headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and ai.app.state.calls == []
