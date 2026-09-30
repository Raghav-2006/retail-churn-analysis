"""Data-quality checks, run automatically at the end of every pipeline run.

Each check returns PASS, WARN or FAIL. Any FAIL raises QualityCheckError, so
a bad load stops the pipeline loudly instead of silently feeding wrong numbers
to the tiering model and the analyst. WARN is for things a human should look
at but that are not necessarily wrong (a volume anomaly in a partial month).

The checks compare the warehouse against the pandas transform output, which
is an independent computation: Postgres recomputes revenue in exact decimal
arithmetic from quantity and price, so a truncated price column, a dropped
batch of rows or a duplicated COPY would all show up here.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from pipeline.load import WAREHOUSE_TABLES

CONTRACT = json.loads((Path(__file__).parent / "contract.json").read_text())

PRIMARY_KEYS = {
    "dim_date": "date_key",
    "dim_customer": "customer_id",
    "dim_product": "stock_code",
    "fact_sales": "sales_line_id",
    "fact_cancellations": "cancellation_line_id",
}
FACT_KEYS = ["invoice", "invoice_date", "date_key", "customer_id", "stock_code", "quantity", "price"]
FOREIGN_KEYS = [("date_key", "dim_date"), ("customer_id", "dim_customer"), ("stock_code", "dim_product")]


def read_frame(conn: psycopg.Connection, sql: str) -> pd.DataFrame:
    cur = conn.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


class QualityCheckError(RuntimeError):
    pass


@dataclass
class Check:
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str


def dtype_family(dtype: object) -> str:
    kind = pd.api.types
    if kind.is_bool_dtype(dtype):
        return "boolean"
    if kind.is_integer_dtype(dtype):
        return "integer"
    if kind.is_float_dtype(dtype):
        return "float"
    if kind.is_datetime64_any_dtype(dtype):
        return "datetime"
    if kind.is_string_dtype(dtype) or dtype == np.dtype(object):
        return "string"
    return str(dtype)


def diff_contract(actual: dict, expected: dict) -> list[str]:
    """Human-readable differences between an actual and expected {column: type} mapping."""
    problems = [f"missing column {c!r}" for c in expected if c not in actual]
    problems += [f"unexpected column {c!r}" for c in actual if c not in expected]
    problems += [
        f"{c!r} is {actual[c]}, expected {t}" for c, t in expected.items() if c in actual and actual[c] != t
    ]
    return problems


def check_raw_schema(raw: pd.DataFrame) -> Check:
    actual = {c: dtype_family(t) for c, t in raw.dtypes.items()}
    problems = diff_contract(actual, CONTRACT["raw"])
    return Check("schema_drift_raw", "FAIL" if problems else "PASS",
                 "; ".join(problems) or f"{len(actual)} columns match the contract")


def check_warehouse_schema(conn: psycopg.Connection) -> Check:
    rows = conn.execute(
        """SELECT table_name, column_name, data_type FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name = ANY(%s)""",
        (WAREHOUSE_TABLES,),
    ).fetchall()
    actual: dict[str, dict] = {t: {} for t in WAREHOUSE_TABLES}
    for table, col, dtype in rows:
        actual[table][col] = dtype
    problems = [
        f"{t}: {p}" for t in WAREHOUSE_TABLES for p in diff_contract(actual[t], CONTRACT["warehouse"][t])
    ]
    n_cols = sum(len(v) for v in actual.values())
    return Check("schema_drift_warehouse", "FAIL" if problems else "PASS",
                 "; ".join(problems) or f"{n_cols} columns in {len(WAREHOUSE_TABLES)} tables match the contract")


def volume_anomalies(monthly: pd.Series, window: int = 6, k: float = 3.0) -> pd.DataFrame:
    """Flag months whose row count is more than k rolling std devs from the rolling median.

    The baseline uses the previous `window` months only (shift(1)), so a month
    is judged against history rather than against itself.
    """
    prior = monthly.shift(1).rolling(window, min_periods=3)
    out = pd.DataFrame({"rows": monthly, "rolling_median": prior.median(), "rolling_std": prior.std()})
    out["z"] = (out["rows"] - out["rolling_median"]) / out["rolling_std"]
    out["anomaly"] = out["z"].abs() > k
    return out


def partial_periods(coverage: pd.DataFrame, min_coverage: float = 0.5) -> pd.DataFrame:
    """Flag months whose data covers less than `min_coverage` of the calendar month.

    `coverage` has one row per month with columns month (first day), first_day, last_day.
    A row-count z-score cannot catch a partial month reliably: the final month of this data
    (9 days of December 2011) sits inside the noise of the Sep-Nov peaks. Measuring the
    calendar span directly does, while the normal Christmas shutdown (sales stop around
    23 December, 74% coverage) is not flagged.
    """
    out = coverage.copy()
    month = pd.to_datetime(out["month"])
    days = month.dt.days_in_month
    span = (pd.to_datetime(out["last_day"]) - pd.to_datetime(out["first_day"])).dt.days + 1
    out["days_in_month"] = days
    out["days_covered"] = span
    out["coverage"] = (span / days).round(3)
    out["partial"] = out["coverage"] < min_coverage
    return out


def run_checks(conn: psycopg.Connection, raw: pd.DataFrame, sales: pd.DataFrame, cancels: pd.DataFrame,
               tables: dict[str, pd.DataFrame]) -> tuple[list[Check], dict]:
    """Run every check. Returns (checks, extra facts for the report)."""
    q = lambda sql, *a: conn.execute(sql, a or None).fetchone()[0]  # noqa: E731
    checks: list[Check] = [check_raw_schema(raw), check_warehouse_schema(conn)]
    extra: dict = {}

    # Row-count reconciliation: what was loaded equals what the transform produced
    for table, expected in [("fact_sales", len(sales)), ("fact_cancellations", len(cancels)),
                            ("dim_customer", len(tables["dim_customer"])),
                            ("dim_product", len(tables["dim_product"])), ("dim_date", len(tables["dim_date"]))]:
        loaded = q(f"SELECT count(*) FROM {table}")
        ok = loaded == expected
        checks.append(Check(f"row_count_{table}", "PASS" if ok else "FAIL",
                            f"loaded {loaded:,} vs {expected:,} after transform"))

    # Revenue reconciliation to the penny (Postgres exact NUMERIC vs pandas float)
    for table, df in [("fact_sales", sales), ("fact_cancellations", cancels)]:
        pg = float(q(f"SELECT coalesce(sum(revenue), 0) FROM {table}"))
        pd_sum = float((df["Quantity"] * df["Price"]).sum())
        diff = abs(pg - pd_sum)
        checks.append(Check(f"revenue_reconciliation_{table}", "PASS" if diff < 0.005 else "FAIL",
                            f"Postgres £{pg:,.2f} vs pandas £{pd_sum:,.2f} (diff £{diff:.4f})"))
        extra[f"{table}_revenue"] = round(pg, 2)

    # No nulls in key columns
    for table in ["fact_sales", "fact_cancellations"]:
        nulls = q(f"SELECT count(*) FROM {table} WHERE " + " OR ".join(f"{c} IS NULL" for c in FACT_KEYS))
        checks.append(Check(f"not_null_keys_{table}", "PASS" if nulls == 0 else "FAIL",
                            f"{nulls:,} rows with a NULL in {', '.join(FACT_KEYS)}"))

    # Business rules: sales have positive quantity and price; cancellations negative quantity
    bad = q("SELECT count(*) FROM fact_sales WHERE quantity <= 0 OR price <= 0")
    checks.append(Check("positive_quantity_price_sales", "PASS" if bad == 0 else "FAIL",
                        f"{bad:,} sales lines with quantity <= 0 or price <= 0"))
    bad = q("SELECT count(*) FROM fact_cancellations WHERE quantity >= 0 OR invoice NOT LIKE 'C%'")
    checks.append(Check("negative_quantity_cancellations", "PASS" if bad == 0 else "FAIL",
                        f"{bad:,} cancellation lines with quantity >= 0 or a non-'C' invoice"))

    # Unique primary keys (constraints enforce this, but a migration that drops them must not go unnoticed)
    dupes = {t: q(f"SELECT count(*) - count(DISTINCT {k}) FROM {t}") for t, k in PRIMARY_KEYS.items()}
    n_dupes = sum(dupes.values())
    checks.append(Check("unique_keys", "PASS" if n_dupes == 0 else "FAIL",
                        "no duplicate keys" if n_dupes == 0 else f"duplicates: {dupes}"))

    # Referential integrity: every fact row joins to its dimensions
    orphans = {}
    for fact in ["fact_sales", "fact_cancellations"]:
        for col, dim in FOREIGN_KEYS:
            orphans[f"{fact}.{col}"] = q(
                f"SELECT count(*) FROM {fact} f LEFT JOIN {dim} d USING ({col}) WHERE d.{col} IS NULL")
    n_orphans = sum(orphans.values())
    checks.append(Check("referential_integrity", "PASS" if n_orphans == 0 else "FAIL",
                        "every fact row joins to date, customer and product" if n_orphans == 0
                        else f"orphans: {orphans}"))

    # Volume anomaly: (a) partial periods, measured by calendar coverage; (b) monthly row counts
    # more than 3 sd from a rolling median of the previous 6 full months
    monthly = read_frame(conn, """
        SELECT date_trunc('month', invoice_date)::date AS month, min(invoice_date)::date AS first_day,
               max(invoice_date)::date AS last_day, count(*) AS rows
        FROM fact_sales GROUP BY 1 ORDER BY 1""")
    cov = partial_periods(monthly)
    full = cov[~cov["partial"]]
    series = pd.Series(full["rows"].to_numpy(), index=[str(m)[:7] for m in full["month"]], dtype=float)
    vol = volume_anomalies(series)
    flagged = vol[vol["anomaly"]]
    parts = [f"{str(r.month)[:7]} is a partial period: data covers {r.first_day} to {r.last_day} "
             f"({r.days_covered} of {r.days_in_month} days, {100 * r.coverage:.0f}%)"
             for r in cov[cov["partial"]].itertuples()]
    parts += [f"{m}: {int(r.rows):,} rows vs median {r.rolling_median:,.0f} (z={r.z:+.1f})"
              for m, r in flagged.iterrows()]
    checks.append(Check("volume_anomaly", "WARN" if parts else "PASS",
                        "; ".join(parts) or f"no partial month and no month beyond 3 sd ({len(cov)} months)"))
    extra["volume_by_month"] = vol.reset_index(names="month").round(2).to_dict(orient="records")
    extra["volume_anomaly_months"] = list(flagged.index)
    extra["partial_periods"] = [
        {"month": str(r.month)[:7], "first_day": str(r.first_day), "last_day": str(r.last_day),
         "days_covered": int(r.days_covered), "days_in_month": int(r.days_in_month), "coverage": float(r.coverage)}
        for r in cov[cov["partial"]].itertuples()]

    # Freshness: the newest loaded invoice equals the newest invoice in the source
    pg_max = q("SELECT max(invoice_date) FROM fact_sales")
    src_max = sales["InvoiceDate"].max() if len(sales) else None
    ok = src_max is not None and pd.Timestamp(pg_max) == pd.Timestamp(src_max)
    checks.append(Check("freshness", "PASS" if ok else "FAIL",
                        f"max invoice_date loaded {pg_max} (source {src_max})"))
    extra["max_invoice_date"] = str(pg_max)
    return checks, extra


def assert_all_pass(checks: list[Check]) -> None:
    failed = [c for c in checks if c.status == "FAIL"]
    if failed:
        lines = "\n".join(f"  - {c.name}: {c.detail}" for c in failed)
        raise QualityCheckError(f"{len(failed)} data-quality check(s) failed:\n{lines}")


def report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"[{c.status}] {c.name:<{width}}  {c.detail}" for c in checks)


def to_records(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
