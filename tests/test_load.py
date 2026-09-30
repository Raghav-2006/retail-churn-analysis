"""Load into a real PostgreSQL (a throwaway schema) and prove it is idempotent."""
import pytest

from pipeline.db import connect
from pipeline.load import build_star, fingerprint, load, table_counts
from pipeline.quality import QualityCheckError, assert_all_pass, run_checks
from pipeline.transform import clean

SCHEMA = "test_retail"


@pytest.fixture
def conn(pg_dsn):
    c = connect(schema=SCHEMA, dsn=pg_dsn)
    yield c
    c.rollback()
    c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.commit()
    c.close()


@pytest.fixture
def cleaned(raw):
    sales, cancels, _, _ = clean(raw)
    return raw, sales, cancels, build_star(sales, cancels)


def test_star_schema_shapes(cleaned):
    _, sales, cancels, tables = cleaned
    assert len(tables["fact_sales"]) == len(sales) == 2
    assert len(tables["fact_cancellations"]) == len(cancels) == 1
    assert set(tables["dim_customer"]["customer_id"]) == {1, 2}
    # dates span 2011-01-05 .. 2011-02-10 inclusive
    assert len(tables["dim_date"]) == 37


def test_load_is_idempotent(conn, cleaned):
    _, _, _, tables = cleaned
    load(conn, tables)
    first_counts, first_print = table_counts(conn), fingerprint(conn)
    load(conn, tables)
    assert table_counts(conn) == first_counts
    assert fingerprint(conn) == first_print
    assert first_counts["fact_sales"] == 2


def test_quality_checks_pass_on_a_good_load(conn, cleaned):
    raw, sales, cancels, tables = cleaned
    load(conn, tables)
    checks, extra = run_checks(conn, raw, sales, cancels, tables)
    assert_all_pass(checks)
    assert extra["fact_sales_revenue"] == 6.25


def test_quality_checks_fail_loudly_on_a_silent_row_loss(conn, cleaned):
    raw, sales, cancels, tables = cleaned
    load(conn, tables)
    conn.execute("DELETE FROM fact_sales WHERE invoice = '101'")
    checks, _ = run_checks(conn, raw, sales, cancels, tables)
    failed = {c.name for c in checks if c.status == "FAIL"}
    assert {"row_count_fact_sales", "revenue_reconciliation_fact_sales"} <= failed
    with pytest.raises(QualityCheckError):
        assert_all_pass(checks)
