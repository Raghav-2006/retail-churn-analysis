"""dbt layer: build the real project on a small synthetic dataset and check it against Python.

Runs `dbt build` (all models + all dbt tests) into throwaway schemas, then asserts that
mart_customer_features equals the v1 SQL and mart_customer_tiers equals tiering/score.py,
customer by customer. The full-data version of this check is tiering/verify_dbt.py.
"""
import numpy as np
import pandas as pd
import pytest
import yaml

from pipeline.db import connect, read_sql
from pipeline.dbt_runner import DBT_DIR, run_dbt, run_results
from pipeline.load import build_star, load
from pipeline.transform import clean
from tests.conftest import make_raw
from tiering.features import FEATURES, build_features, build_features_legacy
from tiering.score import WEIGHTS, score_customers

SRC, OUT = "test_dbt_src", "test_dbt_out"
CUTOFFS = ["2011-04-01", "2011-06-01"]


def test_dbt_weights_match_python():
    project = yaml.safe_load((DBT_DIR / "dbt_project.yml").read_text())
    assert project["vars"]["tier_weights"] == WEIGHTS


def synthetic_raw(n_customers: int = 40, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, inv = [], 1000
    for cid in range(1, n_customers + 1):
        for _ in range(int(rng.integers(1, 7))):                 # 1-6 orders each
            inv += 1
            day = pd.Timestamp("2011-01-03") + pd.Timedelta(days=int(rng.integers(0, 140)))
            for _ in range(int(rng.integers(1, 4))):             # 1-3 lines per order
                code = f"P{int(rng.integers(1, 15)):02d}"
                rows.append((str(inv), code, f"item {code}", int(rng.integers(1, 12)), str(day),
                             float(rng.choice([0.85, 1.25, 2.1, 4.95])), float(cid), "UK" if cid % 4 else "France"))
            if rng.random() < 0.2:                               # some cancellations
                rows.append((f"C{inv}", code, f"item {code}", -1, str(day), 1.25, float(cid), "UK"))
    # a later order for a few customers, to give them future revenue and recency spread
    for cid in range(1, n_customers + 1, 3):
        rows.append((str(inv + cid), "P01", "item P01", 2, "2011-06-15 10:00", 2.1, float(cid), "UK"))
    return make_raw(rows)


@pytest.fixture(scope="module")
def built(pg_dsn):
    sales, cancels, _, _ = clean(synthetic_raw())
    with connect(schema=SRC, dsn=pg_dsn) as conn:
        load(conn, build_star(sales, cancels))
    proc = run_dbt(["build"], source_schema=SRC, target_schema=OUT, check=False,
                   extra_vars={"feature_cutoffs": CUTOFFS, "tier_cutoff": "2011-06-01"})
    yield proc
    with connect(schema=SRC, dsn=pg_dsn) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SRC} CASCADE")
        conn.execute(f"DROP SCHEMA IF EXISTS {OUT} CASCADE")
        conn.commit()


def test_dbt_build_passes_all_models_and_tests(built):
    assert built.returncode == 0, built.stdout[-3000:]
    counts = run_results()
    assert counts["model"] == {"success": 11}
    assert set(counts["test"]) == {"pass"} and counts["test"]["pass"] >= 60


@pytest.mark.parametrize("cutoff", CUTOFFS)
def test_mart_features_equal_v1_sql(built, cutoff):
    mart = build_features(cutoff, schema=OUT)
    legacy = build_features_legacy(cutoff, schema=SRC)
    assert list(mart.columns) == FEATURES
    pd.testing.assert_frame_equal(mart, legacy, check_exact=True)


def test_sql_tiers_equal_python_tiers(built):
    py = score_customers(build_features("2011-06-01", schema=OUT))[["score", "tier"]]
    sql = read_sql("SELECT customer_id, score, tier FROM mart_customer_tiers ORDER BY customer_id",
                   schema=OUT).set_index("customer_id")
    assert (sql["tier"] == py["tier"].reindex(sql.index)).all()
    assert (sql["score"] == py["score"].reindex(sql.index)).all()   # bit-for-bit
    assert sql["tier"].value_counts().to_dict() == {"Tier 1": 4, "Tier 2": 8, "Tier 3": 12, "Tier 4": 16}
