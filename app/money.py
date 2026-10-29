"""Money is stored as signed integer cents. Negative = payment, positive = deposit."""

import re

_MONEY_RE = re.compile(r"^(-)?\$?(\d*)(?:\.(\d{1,2}))?$")

# $9,999,999.99: the most the 9-digit cash-register input in app.js can enter. Keeps
# SUM() over the ledger far from SQLite's 64-bit limit.
MAX_CENTS = 999_999_999


def parse_cents(text: str) -> int:
    """Parse user input like "$1,234.56", "10", ".75" into integer cents."""
    cleaned = text.strip().replace(",", "").replace(" ", "")
    match = _MONEY_RE.match(cleaned)
    if not match or not (match.group(2) or match.group(3)):
        raise ValueError(f"Not a valid amount: {text!r}")
    sign, whole, frac = match.groups()
    cents = int(whole or "0") * 100 + int((frac or "0").ljust(2, "0"))
    if cents > MAX_CENTS:
        raise ValueError(f"Amount too large: {text!r}")
    return -cents if sign else cents


def format_cents(cents: int, signed: bool = True) -> str:
    """Format cents as "$1,234.56" (or "-$1,234.56" when signed and negative)."""
    sign = "-" if signed and cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def plain_amount(cents: int) -> str:
    """Unsigned amount without symbol or separators, for form inputs: "1234.56"."""
    whole, frac = divmod(abs(cents), 100)
    return f"{whole}.{frac:02d}"
