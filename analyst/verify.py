"""Self-verification (Phase 8): do the numbers in the prose answer appear in the SQL result?

    unsupported_numbers(answer, question, columns, rows) -> the numbers that could not be matched

Deterministic, no LLM. A number in the answer is supported when some result cell equals it after
rounding to the precision the answer used, allowing for:
  - thousands separators, currency signs and "million"/"m"/"k"/"bn" suffixes (£17.1 million);
  - percentages written from a fraction (0.253 -> 25.3%) and fractions from a percentage;
  - a sign flip (cancellations are stored negative, reported positive);
  - small derived facts that need no cell: the row count, ranks ("top 5"), numbers that are also
    in the question or written as constants in the executed SQL (the 90 of a 90-day window, the
    1000 of a £1,000 threshold), and calendar numbers (years, days of the month, quarters).
Anything else is flagged, and the agent then rewrites the answer once from the result, and abstains
if the rewrite still has an unsupported number (see Analyst._verify).
"""
import math
import re
from datetime import date, datetime

NUM = re.compile(r"(?<![\w.])[-−]?[£$]?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s?(%|percent|million|mn|m\b|bn|billion|k\b)?",
                 re.IGNORECASE)
MULT = {"million": 1e6, "mn": 1e6, "m": 1e6, "bn": 1e9, "billion": 1e9, "k": 1e3}
MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec").split()


def numbers(text: str) -> list[tuple[float, int, str]]:
    """(value, decimals, raw) for each number in the text; ISO dates and times are skipped."""
    text = re.sub(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?\b", " ", text)
    out = []
    for m in NUM.finditer(text):
        whole, frac, suffix = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
        value = float(whole.replace(",", "") + frac)
        decimals = len(frac) - 1 if frac else 0
        if suffix in MULT:
            value *= MULT[suffix]
            decimals -= int(round(math.log10(MULT[suffix])))
        out.append((value, decimals, m.group(0).strip()))
    return out


def _cell_numbers(v: object) -> list[float]:
    if isinstance(v, bool) or v is None:
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    if isinstance(v, (date, datetime)):
        return [float(v.year), float(v.month), float(v.day)]
    if isinstance(v, str):
        return [n for n, _, _ in numbers(v)]
    return []


def _matches(value: float, decimals: int, cell: float) -> bool:
    for c in (cell, -cell, cell * 100, cell / 100):
        if decimals >= 0 and round(c, decimals) == round(value, decimals):
            return True
        tol = 0.5 * 10 ** (-decimals) if decimals < 0 else 0.0
        if decimals < 0 and abs(c - value) <= tol:
            return True
        if math.isclose(c, value, rel_tol=5e-4, abs_tol=0.005):
            return True
        # an answer may round more coarsely than the precision it prints ("£445.6" for 445.6018...)
        if decimals >= 0 and abs(c - value) <= 0.5 * 10 ** (-decimals) + 1e-9:
            return True
    return False


def _calendar(value: float, raw: str, text: str) -> bool:
    if value.is_integer() and 1900 <= value <= 2100 and "," not in raw and "£" not in raw:
        return True   # a year
    if value.is_integer() and 1 <= value <= 31 and "£" not in raw and "%" not in raw:
        months, n = "|".join(MONTHS), int(value)
        pat = rf"({months})\.?\s+{n}\b|\b{n}(st|nd|rd|th)?\s+(of\s+)?({months})|\bq{n}\b"
        return re.search(pat, text, re.IGNORECASE) is not None
    return False


def sql_literals(sql: str | None) -> set[float]:
    """Numeric constants written in the SQL (a 90-day window, a £1,000 threshold): parameters the answer
    may restate. Quoted dates are dropped first so their parts don't count."""
    if not sql:
        return set()
    bare = re.sub(r"'\d{4}-\d{2}-\d{2}[^']*'", " ", sql)   # dates only: INTERVAL '90 days' keeps its 90
    return {float(m) for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", bare)}


def unsupported_numbers(answer: str, question: str, columns: list[str], rows: list[list],
                        sql: str | None = None) -> list[str]:
    cells = [n for r in rows for v in r for n in _cell_numbers(v)]
    allowed = {float(len(rows))} | {n for n, _, _ in numbers(question)} | sql_literals(sql)
    bad = []
    for value, decimals, raw in numbers(answer):
        small_count = value.is_integer() and 1 <= value <= 10 and decimals == 0 and not re.search(r"[£$%]", raw)
        if value in allowed or small_count or _calendar(value, raw, answer):
            continue
        if not any(_matches(value, decimals, c) for c in cells):
            bad.append(raw)
    return bad
