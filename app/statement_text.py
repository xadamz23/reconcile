"""Pull the transaction text out of a bank statement PDF, locally, before anything is sent
to Claude.

Only the summary block and the transaction sections are kept, found by their headings on
whatever pages they fall (a busy month runs to more pages). Everything else goes: the
address block, the bank's blank balancing form, the daily balance table, fee disclosures.
What's kept is then redacted: every long or masked number (account numbers, the savings
account in transfer lines, reference numbers) and the account holder's name.

Written against Associated Bank's statements. Anything without these headings (another
bank, a scan with no text layer) returns None, and the caller sends the file itself.
"""

import io
import re

# Section headings, as printed. The summary block starts at "Beginning Balance".
START = "Beginning Balance"
SECTIONS = ("Deposits and Other Additions", "Withdrawals and Other Deductions", "Checks Paid")
# The first heading after the last transaction section.
STOPS = ("Total Overdraft Fees", "Balance Summary", "Current Service Fee Period", "Service Fees Disclosure")

PERIOD_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})")
PAGE_HEADER_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+\s*(?P<name>.*?)(?:\s+Acct\s*#\s*\S+)?$")
# Lines that sit among the transactions on the page but belong to the page furniture.
FURNITURE_RE = re.compile(
    r"^(?:Page\s+\d+\s+of\s+\d+.*"
    r"|Member FDIC.*"
    r"|FINANCIAL STATEMENT OF ACCOUNTS"
    r"|Primary Account:.*"
    r"|Statement Activity Period"
    r"|\d{2}/\d{2}/\d{4}\s+to\s+\d{2}/\d{2}/\d{4}"
    r"|Bank:\s*\S+"
    r"|Mail Code:.*"
    r"|>?\d{5}\s+\d{7}\s.*"  # mail barcodes
    r"|DEPOSIT ACCOUNTS"
    r"|FINANCIAL SUMMARY.*"
    r")$",
    re.IGNORECASE,
)
# Account, card and reference numbers: 8+ digits, or a run of X or * masking digits.
LONG_NUMBER_RE = re.compile(r"(?:[X*]{3,}\d+|\d{8,})", re.IGNORECASE)


def _dedupe_page(text: str) -> str:
    """Some PDFs carry a page's text twice (a visible layer and a hidden one). Cut at the
    point where the page's first line comes round again."""
    lines = text.splitlines()
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if not first:
        return text
    seen = False
    for i, ln in enumerate(lines):
        if ln.strip() == first:
            if seen:
                return "\n".join(lines[:i])
            seen = True
    return text


DATED_LINE_RE = re.compile(r"^\s*\d{2}/\d{2}/\d{4}\s", re.MULTILINE)


def _has_transactions(page: str) -> bool:
    """Pages with no summary, no section heading and no dated line (the bank's blank
    balancing form, disclosures) are dropped whole, wherever they fall."""
    return START in page or any(s in page for s in SECTIONS) or bool(DATED_LINE_RE.search(page))


def holder_name(pages: list[str]) -> str | None:
    """The account holder's name, from a continuation page's header
    ("Page 2 of 3ADAM R STAHL Acct #…"), or the line under page 1's "Page 1 of 3"."""
    for page in pages:
        for line in page.splitlines():
            m = PAGE_HEADER_RE.match(line.strip())
            if m and m.group("name").strip():
                return m.group("name").strip()
    for page in pages:
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        for i, line in enumerate(lines[:-1]):
            if re.fullmatch(r"Page\s+1\s+of\s+\d+", line):
                name = lines[i + 1]
                if re.fullmatch(r"[A-Za-z][A-Za-z .,'-]+", name):
                    return name
    return None


def redact(text: str, name: str | None) -> str:
    text = LONG_NUMBER_RE.sub("[number]", text)
    if name:
        # Each part of the name as a whole word, so "SMITH,JANE Q" and "JANE SMITH" both go.
        for part in sorted({p for p in re.split(r"[\s,]+", name) if len(p) >= 2}, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(part)}\b", "[name]", text, flags=re.IGNORECASE)
    return text


def prepare(pages: list[str]) -> str | None:
    """The statement's summary and transactions as redacted text, or None if the headings
    aren't there."""
    pages = [_dedupe_page(p) for p in pages]
    joined = "\n".join(p for p in pages if _has_transactions(p))
    if START not in joined or not any(s in joined for s in SECTIONS):
        return None
    period = PERIOD_RE.search(joined[joined.find("Statement Activity Period"):]) if "Statement Activity Period" in joined else None

    kept: list[str] = []
    keeping = False
    for raw in joined.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not keeping:
            if line.startswith(START):
                keeping = True
            else:
                continue
        if any(line.startswith(s) for s in STOPS):
            # The stops come after Checks Paid; anything after them is the balance table.
            keeping = False
            continue
        if FURNITURE_RE.match(line):
            continue
        kept.append(line)
    if not kept:
        return None

    header = []
    if period:
        header.append(f"Statement Activity Period: {period.group(1)} to {period.group(2)}")
    return redact("\n".join(header + kept), holder_name(pages))


def extract(pdf_bytes: bytes) -> str | None:
    """Redacted statement text from a PDF's text layer, or None (no text layer, an
    unreadable file, or a layout this doesn't know)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:  # pypdf raises many types for damaged or encrypted files
        return None
    return prepare(pages)
