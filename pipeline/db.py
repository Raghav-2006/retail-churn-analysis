"""Database connections.

If DATABASE_URL is set (CI uses a PostgreSQL service container), connect to it.
Otherwise start, or reuse, an embedded PostgreSQL server from `pgserver` whose
data directory lives in data/pgdata, so no Docker or system install is needed.
"""
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
import psycopg
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
PGDATA = ROOT / "data" / "pgdata"
SQL_DIR = ROOT / "sql"
SCHEMA = "retail"  # the warehouse lives in its own schema, not public


@lru_cache(maxsize=1)
def get_dsn() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    import warnings

    warnings.filterwarnings("ignore", message=".*XDG_RUNTIME_DIR.*")
    import pgserver

    # cleanup_mode=None keeps the server running after this process exits,
    # so later runs (notebooks, the analyst) reconnect instantly
    server = pgserver.get_server(PGDATA, cleanup_mode=None)
    return server.get_uri()


def connect(schema: str = SCHEMA, dsn: str | None = None) -> psycopg.Connection:
    """Owner connection used by the pipeline (creates the schema if needed)."""
    conn = psycopg.connect(dsn or get_dsn())
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    conn.execute(f"SET search_path TO {schema}")
    conn.commit()
    return conn


@lru_cache(maxsize=4)
def engine(schema: str = SCHEMA):
    """SQLAlchemy engine for pandas.read_sql, with the warehouse schema on the search path."""
    url = get_dsn().replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})


def read_sql(sql: str, schema: str = SCHEMA, **params) -> pd.DataFrame:
    """Run a query; named parameters use :name syntax. text() also keeps a literal '%' safe."""
    with engine(schema).connect() as con:
        return pd.read_sql_query(text(sql), con, params=params or None)


def run_sql_file(name: str, schema: str = SCHEMA) -> pd.DataFrame:
    """Run one query from sql/analysis/<name>.sql and return it as a DataFrame."""
    return read_sql((SQL_DIR / "analysis" / f"{name}.sql").read_text(), schema=schema)
