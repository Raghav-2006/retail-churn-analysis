import pandas as pd
import pytest

from pipeline.db import connect
from pipeline.load import build_star, load
from pipeline.transform import clean
from tests.conftest import make_raw
from tiering.features import FEATURES, build_dataset
from tiering.score import WEIGHTS, assign_tiers, explain, percentiles, score_customers
from tiering.validate import capture, rounded_weights


def toy_features(n: int = 20) -> pd.DataFrame:
    i = pd.RangeIndex(1, n + 1, name="customer_id")
    return pd.DataFrame({
        "recency": [float(n - k) for k in range(n)],      # customer n bought most recently
        "frequency": [float(k + 1) for k in range(n)],
        "monetary": [100.0 * (k + 1) for k in range(n)],
        "tenure": [300.0] * n,
        "avg_order_value": [100.0] * n,
        "breadth": [float(k + 1) for k in range(n)],
        "cancel_rate": [0.5 - k / (2 * n) for k in range(n)],
        "regularity": [None] * 5 + [float(20 - k) for k in range(5, n)],
    }, index=i)


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_percentiles_are_oriented_so_one_is_best():
    pct = percentiles(toy_features())
    assert pct.loc[20, "recency"] == 1.0 and pct.loc[1, "recency"] == pytest.approx(0.05)
    assert pct.loc[20, "monetary"] == 1.0
    assert pct.loc[20, "cancel_rate"] == 1.0  # lowest cancellation rate is best
    assert (pct.loc[1:5, "regularity"] == 0.0).all()  # unknown regularity ranks last
    assert pct.le(1).all().all() and pct.ge(0).all().all()


def test_best_customer_scores_one_and_lands_in_tier_1():
    scored = score_customers(toy_features())
    assert scored.loc[20, "score"] == pytest.approx(1.0)
    assert scored.loc[20, "tier"] == "Tier 1"
    assert scored.loc[1, "tier"] == "Tier 4"


def test_tier_sizes_follow_10_20_30_40():
    tiers = assign_tiers(pd.Series(range(100), index=range(100), dtype=float))
    assert tiers.value_counts().to_dict() == {"Tier 1": 10, "Tier 2": 20, "Tier 3": 30, "Tier 4": 40}
    # tiers are ordered by score
    assert tiers[99] == "Tier 1" and tiers[0] == "Tier 4"


def test_explain_points_add_up_to_the_score():
    scored = score_customers(toy_features())
    ex = explain(12, scored)
    assert sum(r["points"] for r in ex["breakdown"]) == pytest.approx(ex["score"], abs=0.2)
    assert ex["tier"] == scored.loc[12, "tier"]
    assert "Customer 12" in ex["text"] and "percentile" in ex["text"]
    with pytest.raises(KeyError):
        explain(999, scored)


def test_capture_share():
    score = pd.Series([4, 3, 2, 1], index=[1, 2, 3, 4], dtype=float)
    outcome = pd.Series([50, 0, 30, 20], index=[1, 2, 3, 4], dtype=float)
    assert capture(score, outcome, 0.25) == 0.5
    assert capture(score, outcome, 0.5) == 0.5
    assert capture(score, outcome, 1.0) == 1.0


def test_rounded_weights_sum_to_one():
    w = rounded_weights({"a": 0.129, "b": 0.418, "c": 0.453})
    assert sum(w.values()) == pytest.approx(1.0)


def test_features_respect_the_cutoff(pg_dsn):
    """Features only see data before the cutoff; the outcome only sees data after it."""
    raw = make_raw([
        ("1", "P1", "x", 1, "2011-01-01", 10.0, 1.0, "UK"),
        ("2", "P2", "y", 2, "2011-01-11", 10.0, 1.0, "UK"),
        ("3", "P1", "x", 1, "2011-01-31", 10.0, 1.0, "UK"),
        ("C4", "P1", "x", -1, "2011-02-01", 10.0, 1.0, "UK"),
        ("5", "P1", "x", 5, "2011-03-15", 10.0, 1.0, "UK"),   # after cutoff: outcome only
        ("6", "P1", "x", 1, "2011-02-20", 7.0, 2.0, "UK"),
    ])
    sales, cancels, _, _ = clean(raw)
    conn = connect(schema="test_tiering", dsn=pg_dsn)
    try:
        load(conn, build_star(sales, cancels))
        data = build_dataset("2011-03-01", "2011-04-01", schema="test_tiering")
    finally:
        conn.execute("DROP SCHEMA test_tiering CASCADE")
        conn.commit()
        conn.close()
    c1 = data.loc[1]
    assert c1["frequency"] == 3 and c1["monetary"] == 40.0
    assert c1["recency"] == 29          # 2011-01-31 -> 2011-03-01
    assert c1["tenure"] == 59           # 2011-01-01 -> 2011-03-01
    assert c1["breadth"] == 2
    assert c1["cancel_rate"] == 0.25    # 1 cancelled of 4 orders
    assert c1["regularity"] == pytest.approx(7.0710678)  # std of gaps [10, 20]
    assert c1["future_revenue"] == 50.0
    assert data.loc[2, "future_revenue"] == 0.0 and pd.isna(data.loc[2, "regularity"])
    assert list(data.columns) == FEATURES + ["future_revenue"]
