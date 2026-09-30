import os

import pandas as pd
import pytest

COLUMNS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]


def make_raw(rows: list[tuple]) -> pd.DataFrame:
    """A tiny raw table with the same columns and dtypes as the real extract."""
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df.astype({
        "Invoice": "string", "StockCode": "string", "Description": "string", "Quantity": "int64",
        "InvoiceDate": "datetime64[ns]", "Price": "float64", "Customer ID": "float64", "Country": "string",
    })


@pytest.fixture
def raw():
    t = "2011-01-05 10:00"
    return make_raw([
        ("100", "A1", "MUG", 2, t, 1.50, 1.0, "United Kingdom"),       # kept
        ("100", "A1", "MUG", 2, t, 1.50, 1.0, "United Kingdom"),       # exact duplicate -> dropped
        ("101", "B2", "PLATE", 1, "2011-02-10 09:00", 3.25, 2.0, "France"),  # kept
        ("102", "A1", "MUG", 5, t, 1.50, None, "United Kingdom"),     # no customer -> dropped
        ("C103", "A1", "MUG", -1, t, 1.50, 1.0, "United Kingdom"),    # cancellation -> separated
        ("104", "A1", "MUG", 0, t, 1.50, 2.0, "France"),              # zero quantity -> dropped
        ("105", "B2", "PLATE", 3, t, 0.0, 2.0, "France"),             # zero price -> dropped
        ("106", "POST", "POSTAGE", 1, t, 18.0, 2.0, "France"),        # non-product -> dropped
        ("107", "ADJUST2", "Adjustment", 1, t, 5.0, 1.0, "United Kingdom"),  # ADJUST* prefix -> dropped
        ("108", "TEST001", "test", 1, t, 1.0, 1.0, "United Kingdom"),  # TEST* prefix -> dropped
        ("109", "C2", "CARRIAGE", 1, t, 50.0, 1.0, "United Kingdom"),  # non-product -> dropped
    ])


@pytest.fixture(scope="session")
def pg_dsn():
    """A real PostgreSQL: DATABASE_URL in CI, the embedded pgserver locally."""
    from pipeline.db import get_dsn

    try:
        dsn = get_dsn()
        import psycopg

        psycopg.connect(dsn).close()
        return dsn
    except Exception as err:  # pragma: no cover
        if os.environ.get("CI"):
            raise
        pytest.skip(f"no PostgreSQL available: {err}")
