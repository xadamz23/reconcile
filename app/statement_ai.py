"""Have Claude read a bank statement into structured lines.

Claude only transcribes. Amounts come back as printed (always positive) with the section
each line was under, and the sign is applied here from the section, so Claude never
decides whether money went in or out. Matching against the register happens elsewhere
(app/statement_match.py), on this machine.

Statements are PDFs. For one with a text layer (the bank's monthly statement), only
redacted text from app.statement_text goes out. A PDF without one (a scan) is sent as is.
"""

import base64
import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app import statement_text
from app.money import MAX_CENTS, parse_cents

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
TIMEOUT_S = 90  # Cloudflare gives up on a request at 100s; the page says Claude gives up at 90
SIGN = {"deposit": 1, "withdrawal": -1, "check": -1}

SYSTEM = """You transcribe a checking account statement into structured data. Copy what is printed; do not calculate, guess or reformat anything except dates.

The statement has a summary (beginning balance, totals for each section, ending balance and closing date) and up to three transaction sections:
- Deposits and Other Additions (money in): section "deposit"
- Withdrawals and Other Deductions (money out, including bank fees, ATM withdrawals and transfers out): section "withdrawal"
- Checks Paid (a table of date, check number and amount, sometimes laid out in two columns side by side): section "check"
Other banks may name these differently (Credits, Debits, Checks); map them to the same three sections. If a statement has a single signed column instead, put negative amounts under "withdrawal" and positive ones under "deposit".

Rules:
- One line per transaction, in the order printed. Sections can continue across pages under "(continued)".
- A long description can wrap onto the next line, often a reference number, before its amount. Join it into one description.
- Skip anything that is not a transaction: TOTAL rows, the summary itself, daily balance tables, overdraft fee summaries, minimum balance notes, waived service charges, and any blank balancing form.
- amount: the line's amount exactly as printed, without a sign (for example "1,620.66").
- date: the line's date as YYYY-MM-DD. If the statement prints dates without a year, use the year from the statement period.
- description: the line's full description as printed. Text such as [number] or [name] is a redaction; keep it as is.
- payee: a short, plain name for who the money went to or came from, as a person would write it in a check register: "Starbucks", "Casey's", "Venmo", "Payroll", "Transfer to savings", "ATM withdrawal", "ATM fee". For a check with no description, use "Check".
- check_number: the check number for lines in Checks Paid, otherwise null.
- The summary amounts are copied as printed too, without a sign. Use "0.00" for a section total the statement doesn't show.
- closing_date is the statement's ending date (the "ending balance on" date or the end of the statement period), as YYYY-MM-DD. period_start is the start of the statement period, or null."""


class Line(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    section: Literal["deposit", "withdrawal", "check"]
    description: str
    payee: str
    amount: str = Field(description="As printed, no sign, e.g. 1,620.66")
    check_number: str | None


class Read(BaseModel):
    period_start: str | None = Field(description="YYYY-MM-DD, or null")
    closing_date: str = Field(description="YYYY-MM-DD")
    beginning_balance: str
    deposits_total: str
    withdrawals_total: str
    checks_total: str
    ending_balance: str = Field(description="May be negative; keep a leading minus if printed")
    lines: list[Line]


class StatementReadError(Exception):
    """Something the person can act on: shown on the Reconcile page."""


def _cents(text: str, what: str) -> int:
    try:
        cents = parse_cents(text.replace("$", "").strip())
    except ValueError:
        raise StatementReadError(f"Couldn't read the {what} ({text!r}) from the statement.") from None
    if abs(cents) > MAX_CENTS:
        raise StatementReadError(f"The {what} on the statement is out of range.")
    return cents


def _date(text: str | None, what: str) -> str | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise StatementReadError(f"Couldn't read the {what} ({text!r}) from the statement.") from None


def to_statement(read: Read, filename: str, source: str) -> dict:
    """Validate Claude's transcription and turn it into what's stored with the draft:
    signed integer cents, ISO dates."""
    lines = []
    for i, ln in enumerate(read.lines):
        cents = abs(_cents(ln.amount, f"amount on line {i + 1}"))
        if cents == 0:
            continue
        number = (ln.check_number or "").strip().lstrip("0") or None
        lines.append({
            "date": _date(ln.date, f"date on line {i + 1}"),
            "section": ln.section,
            "description": " ".join(ln.description.split()),
            "payee": " ".join(ln.payee.split()) or "Bank",
            "cents": SIGN[ln.section] * cents,
            "number": number if ln.section == "check" or number else None,
        })
    if not lines:
        raise StatementReadError("Claude didn't find any transactions on that statement.")
    return {
        "file": filename,
        "source": source,  # "text": redacted text was sent; "file": the file itself was
        "period_start": _date(read.period_start, "statement period"),
        "closing_date": _date(read.closing_date, "closing date"),
        "beginning_cents": _cents(read.beginning_balance, "beginning balance"),
        "deposits_cents": abs(_cents(read.deposits_total, "deposits total")),
        "withdrawals_cents": abs(_cents(read.withdrawals_total, "withdrawals total")),
        "checks_cents": abs(_cents(read.checks_total, "checks total")),
        "ending_cents": _cents(read.ending_balance, "ending balance"),
        "lines": lines,
    }


def _content(data: bytes, media_type: str) -> tuple[list[dict], str]:
    if media_type != "application/pdf" or not data.startswith(b"%PDF"):
        raise StatementReadError("That isn't a PDF. Choose your monthly statement PDF from the bank.")
    text = statement_text.extract(data)
    if text:
        return [{"type": "text", "text": "Here is the statement:\n\n" + text}], "text"
    block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.standard_b64encode(data).decode()}}
    return [block, {"type": "text", "text": "Here is the statement."}], "file"


def read_statement(data: bytes, media_type: str, filename: str) -> dict:
    """Read a statement file into the stored statement dict. Raises StatementReadError."""
    import anthropic

    content, source = _content(data, media_type)
    # No retries: a retry after a slow read would run past Cloudflare's limit anyway, and the
    # person can simply try again.
    client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low"},
            output_format=Read,
            # If a safety classifier declines, retry on the model the API picks.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise StatementReadError("The Claude API key was refused. Check ANTHROPIC_API_KEY on the server.") from None
    except anthropic.RateLimitError:
        raise StatementReadError("Claude is busy right now. Try again in a minute.") from None
    except anthropic.APITimeoutError:
        raise StatementReadError("Reading the statement took too long. Try again, or try a smaller file.") from None
    except anthropic.APIConnectionError:
        raise StatementReadError("Couldn't reach Claude. Check the server's connection and try again.") from None
    except anthropic.APIStatusError as e:
        log.warning("Statement read failed: %s %s", e.status_code, getattr(e, "request_id", ""))
        raise StatementReadError(f"Claude couldn't read the statement (error {e.status_code}). Try again.") from None

    if response.stop_reason == "refusal":
        raise StatementReadError("Claude declined to read that file.")
    if response.stop_reason == "max_tokens":
        raise StatementReadError("That statement was too long to read in one go.")
    read = response.parsed_output
    if read is None:
        raise StatementReadError("Claude's answer couldn't be read. Try again.")
    return to_statement(read, filename, source)
