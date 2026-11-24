"""Line up a statement Claude read with the register's open items. Pure code, no AI: the
money side is decided here, exactly.

Passes, each one-to-one:
1. A statement check and a register item with the same check number and amount.
2. The same amount within WINDOW_DAYS, nearest date first (payee similarity breaks ties),
   so repeats like two $6.25 coffees on one day pair off in order.
3. "Amount differs": a leftover line and item close in amount, date and payee. These are
   flagged for the person to fix, never matched.
"""

import difflib
import re
from dataclasses import dataclass, field
from datetime import date

WINDOW_DAYS = 10
NEAR_CENTS, NEAR_SHARE = 500, 0.05  # "Amount differs": within $5 or 5%
NEAR_PAYEE = 0.5  # and at least this similar by name (or the same check number)
SECTION_NAMES = {"deposit": "Deposits", "withdrawal": "Withdrawals", "check": "Checks"}


@dataclass
class Review:
    statement: dict
    matched: list[tuple[int, dict]] = field(default_factory=list)  # (line index, txn)
    differs: list[tuple[int, dict]] = field(default_factory=list)  # (line index, txn)
    missing: list[int] = field(default_factory=list)  # line indexes with nothing in the register
    unmatched: list[dict] = field(default_factory=list)  # open items not on the statement
    warnings: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[dict]:
        return self.statement["lines"]

    @property
    def matched_ids(self) -> set[int]:
        return {t["id"] for _, t in self.matched}

    @property
    def differs_by_txn(self) -> dict[int, int]:
        return {t["id"]: i for i, t in self.differs}

    @property
    def ready(self) -> list[dict]:
        """Matched items not ticked yet: what Tick matched ticks."""
        return [t for _, t in self.matched if t["status"] == "unverified"]

    @property
    def ticked_count(self) -> int:
        return sum(1 for _, t in self.matched if t["status"] == "verified")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", name.lower().replace("'", "")).strip()


def similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _number(n) -> str | None:
    return (str(n).strip().lstrip("0") or None) if n else None


def match(statement: dict, candidates: list[dict]) -> Review:
    """candidates: the reconcile's open items (dicts with id, date, payee, amount_cents,
    number, status)."""
    review = Review(statement)
    lines = statement["lines"]
    free_lines = set(range(len(lines)))
    free_txns = {t["id"]: t for t in candidates}

    def take(i, t, into):
        into.append((i, t))
        free_lines.discard(i)
        free_txns.pop(t["id"], None)

    # 1. Checks by number.
    for i, ln in enumerate(lines):
        num = _number(ln.get("number"))
        if not num:
            continue
        for t in list(free_txns.values()):
            if _number(t["number"]) == num and t["amount_cents"] == ln["cents"]:
                take(i, t, review.matched)
                break

    # 2. Same amount, nearest date. Every candidate pair, best first, taken greedily.
    pairs = []
    for i in free_lines:
        ln = lines[i]
        for t in free_txns.values():
            if t["amount_cents"] != ln["cents"]:
                continue
            days = _days(ln["date"], t["date"])
            if days <= WINDOW_DAYS:
                pairs.append((days, -similarity(ln["payee"], t["payee"]), ln["date"], t["date"], i, t["id"]))
    for *_, i, tid in sorted(pairs):
        if i in free_lines and tid in free_txns:
            take(i, free_txns[tid], review.matched)

    # 3. Near misses, flagged only.
    near = []
    for i in free_lines:
        ln = lines[i]
        for t in free_txns.values():
            a, b = ln["cents"], t["amount_cents"]
            if (a < 0) != (b < 0) or a == b:
                continue
            gap = abs(a - b)
            if gap > max(NEAR_CENTS, NEAR_SHARE * max(abs(a), abs(b))):
                continue
            days = _days(ln["date"], t["date"])
            if days > WINDOW_DAYS:
                continue
            same_check = _number(ln.get("number")) and _number(ln.get("number")) == _number(t["number"])
            sim = similarity(ln["payee"], t["payee"])
            if not same_check and sim < NEAR_PAYEE:
                continue
            near.append((gap, days, -sim, i, t["id"]))
    for *_, i, tid in sorted(near):
        if i in free_lines and tid in free_txns:
            take(i, free_txns[tid], review.differs)

    review.matched.sort(key=lambda p: p[0])
    review.missing = sorted(free_lines, key=lambda i: (lines[i]["date"], i))
    review.unmatched = sorted(free_txns.values(), key=lambda t: (t["date"], t["id"]))
    review.warnings = totals_check(statement)
    return review


def totals_check(statement: dict) -> list[str]:
    """Warnings when the lines don't add up to the statement's own totals."""
    from app.money import format_cents

    out = []
    sums = {"deposit": 0, "withdrawal": 0, "check": 0}
    for ln in statement["lines"]:
        sums[ln["section"]] += abs(ln["cents"])
    printed = {"deposit": statement["deposits_cents"], "withdrawal": statement["withdrawals_cents"],
               "check": statement["checks_cents"]}
    for section, total in printed.items():
        if sums[section] != total:
            out.append(f"{SECTION_NAMES[section]} add up to {format_cents(sums[section])}, "
                       f"the statement says {format_cents(total)}.")
    expected = (statement["beginning_cents"] + printed["deposit"] - printed["withdrawal"] - printed["check"])
    if expected != statement["ending_cents"]:
        out.append(f"The statement's totals come to {format_cents(expected)}, "
                   f"but its ending balance is {format_cents(statement['ending_cents'])}.")
    return out


def suggest_payee(name: str, payees: list[str]) -> str:
    """The register's own spelling of a payee when one is close enough ("Caseys" →
    "Casey's"), otherwise Claude's clean name."""
    best, score = None, 0.0
    for p in payees:
        s = difflib.SequenceMatcher(None, _norm(name), _norm(p)).ratio()
        if _norm(name) == _norm(p):
            return p
        if s > score:
            best, score = p, s
    return best if best and score >= 0.8 else name
