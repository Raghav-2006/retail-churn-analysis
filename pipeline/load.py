"""Load: reshape the cleaned tables into a star schema and load them into PostgreSQL.

Idempotency: every load runs inside ONE transaction that truncates the five
warehouse tables and bulk-loads them again with COPY. Running the pipeline
twice therefore gives identical tables, and a failure half-way rolls back to
the previous good load instead of leaving a half-empty warehouse. Surrogate
keys (sales_line_id, ...) come from the deterministic row order of the
transform, so they are identical across runs too.
"""
import io

import pandas as pd
import psycopg

from pipeline.db import SCHEMA, SQL_DIR
from pipeline.transform import is_non_product

WAREHOUSE_TABLES = ["dim_date", "dim_customer", "dim_product", "fact_sales", "fact_cancellations"]
READONLY_ROLE = "analyst_ro"


def _mode(df: pd.DataFrame, key: str, col: str) -> pd.Series:
    """Most frequent non-null value of `col` per `key`; ties go to the alphabetically first value."""
    counts = df.dropna(subset=[col]).groupby([key, col]).size().rename("n").reset_index()
    counts = counts.sort_values([key, "n", col], ascending=[True, False, True])
    return counts.drop_duplicates(key).set_index(key)[col]


def _fact(df: pd.DataFrame, id_col: str) -> pd.DataFrame:
    out = pd.DataFrame({
        id_col: range(1, len(df) + 1),
        "invoice": df["Invoice"].to_numpy(),
        "invoice_date": df["InvoiceDate"].to_numpy(),
        "date_key": df["InvoiceDate"].dt.strftime("%Y%m%d").astype(int).to_numpy(),
        "customer_id": df["Customer ID"].astype(int).to_numpy(),
        "stock_code": df["StockCode"].to_numpy(),
        "country": df["Country"].to_numpy(),
        "quantity": df["Quantity"].astype(int).to_numpy(),
        "price": df["Price"].to_numpy(),
    })
    return out


def build_star(sales: pd.DataFrame, cancels: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Turn the transform output (sales, cancellations) into star-schema tables."""
    both = pd.concat([sales, cancels], ignore_index=True)

    # dim_date: one row per calendar day between the first and last transaction
    days = pd.date_range(both["InvoiceDate"].min().normalize(), both["InvoiceDate"].max().normalize(), freq="D")
    dim_date = pd.DataFrame({
        "date_key": days.strftime("%Y%m%d").astype(int),
        "full_date": days.date,
        "year": days.year,
        "quarter": days.quarter,
        "month": days.month,
        "month_name": days.strftime("%B"),
        "month_start": days.to_period("M").to_timestamp().date,
        "day_of_month": days.day,
        "day_of_week": days.dayofweek + 1,
        "day_name": days.strftime("%A"),
        "is_weekend": days.dayofweek >= 5,
    })

    # dim_customer: every customer on either fact table; country = their most common one
    customers = pd.Index(sorted(both["Customer ID"].astype(int).unique()), name="customer_id")
    span = sales.groupby("Customer ID")["InvoiceDate"].agg(["min", "max"])
    dim_customer = pd.DataFrame({
        "customer_id": customers,
        "country": _mode(both.assign(cid=both["Customer ID"].astype(int)), "cid", "Country").reindex(customers).to_numpy(),
        "first_invoice_date": span["min"].reindex(customers).to_numpy(),
        "last_invoice_date": span["max"].reindex(customers).to_numpy(),
    })

    # dim_product: every stock code on either fact; cancellations can reference postage/fee codes
    codes = pd.Index(sorted(both["StockCode"].unique()), name="stock_code")
    desc = _mode(sales, "StockCode", "Description").combine_first(_mode(cancels, "StockCode", "Description"))
    dim_product = pd.DataFrame({
        "stock_code": codes,
        "description": desc.reindex(codes).to_numpy(),
        "is_product": ~is_non_product(pd.Series(codes, dtype="string")).to_numpy(),
    })

    return {
        "dim_date": dim_date,
        "dim_customer": dim_customer,
        "dim_product": dim_product,
        "fact_sales": _fact(sales, "sales_line_id"),
        "fact_cancellations": _fact(cancels, "cancellation_line_id"),
    }


def create_schema(conn: psycopg.Connection) -> None:
    conn.execute((SQL_DIR / "schema.sql").read_text())


def _copy(conn: psycopg.Connection, table: str, df: pd.DataFrame) -> None:
    buf = io.StringIO()
    # CSV format: an unquoted empty field is NULL, which is how pandas writes NaN/NaT
    df.to_csv(buf, index=False, header=False)
    cols = ", ".join(df.columns)
    with conn.cursor().copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv)") as copy:
        copy.write(buf.getvalue())


def load(conn: psycopg.Connection, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Truncate-and-load all warehouse tables in one transaction. Returns row counts loaded."""
    with conn.transaction():
        create_schema(conn)
        conn.execute("TRUNCATE " + ", ".join(WAREHOUSE_TABLES))
        # Dimensions first so the fact tables' foreign keys resolve
        for name in WAREHOUSE_TABLES:
            _copy(conn, name, tables[name])
        conn.execute(
            """INSERT INTO etl_run_log (sales_rows, cancel_rows, sales_revenue, max_invoice_date, fingerprint)
               SELECT (SELECT count(*) FROM fact_sales), (SELECT count(*) FROM fact_cancellations),
                      (SELECT coalesce(sum(revenue), 0) FROM fact_sales), (SELECT max(invoice_date) FROM fact_sales),
                      %s""",
            (fingerprint(conn),),
        )
    for name in WAREHOUSE_TABLES:
        conn.execute(f"ANALYZE {name}")
    conn.commit()
    return {name: len(df) for name, df in tables.items()}


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in WAREHOUSE_TABLES}


def fingerprint(conn: psycopg.Connection) -> str:
    """One md5 over the full, key-ordered content of every warehouse table."""
    from pipeline.quality import PRIMARY_KEYS

    parts = [
        conn.execute(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY {PRIMARY_KEYS[name]}), '')) FROM {name} t")
        .fetchone()[0]
        for name in WAREHOUSE_TABLES
    ]
    return conn.execute("SELECT md5(%s)", ("".join(parts),)).fetchone()[0]


def previous_fingerprints(conn: psycopg.Connection, n: int = 2) -> list[str]:
    rows = conn.execute("SELECT fingerprint FROM etl_run_log ORDER BY run_id DESC LIMIT %s", (n,)).fetchall()
    return [r[0] for r in rows]


def grant_readonly(conn: psycopg.Connection, schema: str = SCHEMA, password: str = READONLY_ROLE) -> None:
    """A login role that can only SELECT from the warehouse; the AI analyst connects as this role."""
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READONLY_ROLE,)).fetchone()
    if not exists:
        conn.execute(f"CREATE ROLE {READONLY_ROLE} LOGIN PASSWORD '{password}'")
    conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO {READONLY_ROLE}")
    conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {READONLY_ROLE}")
    conn.execute(f"ALTER ROLE {READONLY_ROLE} SET default_transaction_read_only = on")
    conn.execute(f"ALTER ROLE {READONLY_ROLE} SET statement_timeout = '15s'")
    conn.commit()
