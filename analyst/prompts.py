"""Prompt versions for the analyst. v1 is the baseline; later versions add one mitigation each."""
from dataclasses import dataclass
from typing import Callable

SCHEMA = """PostgreSQL warehouse (schema `retail`, already on the search_path). UK online gift wholesaler, GBP.

fact_sales          one row per invoice line of a completed sale
  sales_line_id BIGINT PK, invoice TEXT, invoice_date TIMESTAMP, date_key INTEGER -> dim_date,
  customer_id INTEGER -> dim_customer, stock_code TEXT -> dim_product, country TEXT,
  quantity INTEGER, price NUMERIC, revenue NUMERIC (= quantity * price)
fact_cancellations  one row per cancelled invoice line (invoice starts with 'C')
  cancellation_line_id BIGINT PK, invoice TEXT, invoice_date TIMESTAMP, date_key INTEGER,
  customer_id INTEGER, stock_code TEXT, country TEXT, quantity INTEGER (negative), price NUMERIC,
  revenue NUMERIC (negative)
dim_customer        customer_id INTEGER PK, country TEXT, first_invoice_date TIMESTAMP, last_invoice_date TIMESTAMP
dim_product         stock_code TEXT PK, description TEXT, is_product BOOLEAN
dim_date            date_key INTEGER PK (YYYYMMDD), full_date DATE, year, quarter, month SMALLINT, month_name TEXT,
                    month_start DATE, day_of_month, day_of_week SMALLINT (1=Mon), day_name TEXT, is_weekend BOOLEAN
"""

OUTPUT = """Respond with JSON only, in exactly this shape:
{"abstain": false, "sql": "<one PostgreSQL SELECT statement>"}
or, if the question cannot be answered from these tables:
{"abstain": true, "reason": "<one sentence on what data is missing>"}"""

V1_SYSTEM = f"""You are a data analyst who answers business questions by writing SQL.

{SCHEMA}
Rules: write a single read-only PostgreSQL SELECT (or WITH ... SELECT) statement. If the question
cannot be answered from these tables, abstain instead of guessing.

{OUTPUT}"""


# v2 mitigation: a data dictionary with the business's metric definitions, plus a no-proxy rule.
# Motivated by the two failure modes seen with v1 (never by specific eval questions):
#   1. definition drift: "revenue" silently computed net of cancellations, contradicting the
#      official figure every report uses;
#   2. fabricated proxies: a formula invented for a metric the data does not contain.
GLOSSARY = """Business definitions. These are the company's official metric definitions; use them exactly
unless the question explicitly asks for something else:
- revenue / sales / spend / "made" = SUM(fact_sales.revenue): GROSS sales. Cancellations are NOT subtracted.
  Only when the question says "net" or "after cancellations/returns", add SUM(fact_cancellations.revenue)
  over the same period (those values are already negative).
- order = one distinct fact_sales.invoice; order value = total revenue of one invoice.
- customer (who bought) = DISTINCT fact_sales.customer_id. dim_customer also lists customers who only cancelled.
- returns = cancellations; units returned = -SUM(fact_cancellations.quantity).
- country of a sale = fact_sales.country.
- time periods: filter fact tables on invoice_date with half-open ranges, e.g.
  invoice_date >= '2011-01-01' AND invoice_date < '2012-01-01'. Data covers 2009-12-01 to 2011-12-09;
  December 2011 is a partial month.
- percentages: return 0-100 values when a percentage is asked for.

Answerability. The tables above are the ONLY data. Before writing SQL, identify the columns each concept in
the question needs. If any concept has no column and is not a direct aggregate of columns, abstain.
Never substitute a proxy formula or an assumption for a metric the data does not contain."""

V2_SYSTEM = f"""You are a data analyst who answers business questions by writing SQL.

{SCHEMA}
{GLOSSARY}

Rules: write a single read-only PostgreSQL SELECT (or WITH ... SELECT) statement. If the question
cannot be answered from these tables, abstain instead of guessing.

{OUTPUT}"""


@dataclass
class Prompt:
    version: str
    system: str
    user: Callable[[str], str]
    retry: Callable[[str, str, str], str]
    self_check: bool = False


def _user(question: str) -> str:
    return f"Question: {question}"


def _retry(question: str, sql: str, error: str) -> str:
    return (f"Question: {question}\n\nYour previous SQL failed.\nSQL:\n{sql}\nError: {error}\n\n"
            "Fix the SQL (or abstain if the question cannot be answered). Same JSON format.")


def check(question: str, sql: str, preview: str) -> str:
    """v3 mitigation: review the executed SQL and a preview of its result before answering."""
    return f"""Question: {question}

SQL that was executed:
{sql}

Result preview (JSON, first rows, plus total row count):
{preview}

Review this before it is shown to a business user. Check, in order:
1. Does the SQL answer exactly this question, using the business definitions?
2. Join fan-out: does it join two fact tables (or a fact to a many-row table) so rows are double counted?
3. Window functions: is the data filtered BEFORE LAG/LEAD/running totals, so the first rows lose their history?
4. Period boundaries, grouping, and units (percent vs fraction, positive vs negative) match the question.
5. Is the result plausible (not NULL where a number is expected, not orders of magnitude off)?
6. Does it rely on a proxy for a metric the data does not contain? Then abstain.

Respond with JSON only:
{{"verdict": "ok"}}  or  {{"verdict": "fix", "sql": "<corrected single SELECT>"}}  or
{{"abstain": true, "reason": "<one sentence>"}}"""


def narrate(question: str, sql: str, result_json: str) -> str:
    return (f"Question: {question}\nSQL that was run:\n{sql}\nResult (JSON):\n{result_json}\n\n"
            "Answer the question in one or two plain sentences using only these numbers. "
            "Revenue is in GBP (£). Do not mention SQL.")


VERSIONS = {
    "v1": Prompt("v1", V1_SYSTEM, _user, _retry),
    "v2": Prompt("v2", V2_SYSTEM, _user, _retry),
    "v3": Prompt("v3", V2_SYSTEM, _user, _retry, self_check=True),
}


def get(version: str) -> Prompt:
    return VERSIONS[version]
