"""Prove the dbt migration changed nothing: tier outputs must match v1 exactly.

    python -m tiering.verify_dbt       # after `dbt build`; writes metrics/dbt.json

Three equality checks, customer by customer, with no tolerance:
  1. features: analytics.mart_customer_features == the v1 SQL over the landing tables,
     for both cutoffs (all 8 features, NaN == NaN for missing regularity);
  2. Python tiers: score_customers(features from the mart) == metrics/v1_tiers.csv, the
     output frozen from the untouched v1 code before Phase 5 began (score to the last bit);
  3. SQL tiers: analytics.mart_customer_tiers (the dbt twin of score.py) == v1 as well.
Any mismatch raises, so this fails loudly in the pipeline and in the Airflow DAG.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.db import read_sql
from pipeline.dbt_runner import ANALYTICS_SCHEMA, run_results
from src.metrics import save_metrics
from tiering.features import FEATURES, build_features, build_features_legacy
from tiering.score import score_customers

ROOT = Path(__file__).resolve().parents[1]
V1_TIERS = ROOT / "metrics" / "v1_tiers.csv"
CUTOFFS = ["2010-12-01", "2011-06-01"]


class ParityError(AssertionError):
    pass


def load_v1_tiers() -> pd.DataFrame:
    v1 = pd.read_csv(V1_TIERS, index_col="customer_id", dtype={"score": str})
    v1["score"] = v1["score"].map(float)  # stored with repr(), so this round-trips exactly
    return v1


def compare_features(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    same_ids = a.index.equals(b.index)
    mismatched = 0 if not same_ids else int(
        (~((a.to_numpy() == b.to_numpy()) | (np.isnan(a.to_numpy()) & np.isnan(b.to_numpy())))).sum())
    return {"customers": len(a), "same_customers": bool(same_ids), "mismatched_values": mismatched,
            "values_compared": int(a.size)}


def compare_tiers(new: pd.DataFrame, v1: pd.DataFrame) -> dict:
    new = new.reindex(v1.index)
    return {
        "customers": len(v1),
        "same_customers": bool(new["tier"].notna().all() and len(new) == len(v1)),
        "tier_mismatches": int((new["tier"] != v1["tier"]).sum()),
        "score_mismatches_exact": int((new["score"] != v1["score"]).sum()),
        "max_abs_score_diff": float((new["score"] - v1["score"]).abs().max()),
    }


def main(schema: str = ANALYTICS_SCHEMA, v1_path: Path = V1_TIERS) -> dict:
    features = {}
    for c in CUTOFFS:
        mart = build_features(c, schema)
        legacy = build_features_legacy(c)
        features[c] = compare_features(mart, legacy)

    v1 = load_v1_tiers()
    py = score_customers(build_features("2011-06-01", schema))[["score", "tier"]]
    sql = read_sql("SELECT customer_id, score, tier FROM mart_customer_tiers", schema=schema).set_index("customer_id")
    result = {
        "features": features,
        "python_tiers_vs_v1": compare_tiers(py, v1),
        "sql_tiers_vs_v1": compare_tiers(sql, v1),
    }
    ok = (all(f["same_customers"] and f["mismatched_values"] == 0 for f in features.values())
          and result["python_tiers_vs_v1"]["tier_mismatches"] == 0
          and result["python_tiers_vs_v1"]["score_mismatches_exact"] == 0
          and result["python_tiers_vs_v1"]["same_customers"]
          and result["sql_tiers_vs_v1"]["tier_mismatches"] == 0
          and result["sql_tiers_vs_v1"]["same_customers"])
    result["matches_v1_exactly"] = ok
    print(result)
    if not ok:
        raise ParityError(f"dbt outputs differ from v1: {result}")
    return result


def dbt_inventory() -> dict:
    """Model and test counts by type, from the last dbt build's manifest."""
    import json
    from collections import Counter

    from pipeline.dbt_runner import DBT_DIR

    nodes = json.loads((DBT_DIR / "target" / "manifest.json").read_text())["nodes"].values()
    tests = Counter(n.get("test_metadata", {}).get("name", f"custom: {n['name']}")
                    for n in nodes if n["resource_type"] == "test")
    models = Counter(n["path"].split("/")[0] for n in nodes if n["resource_type"] == "model")
    return {"test_types": dict(tests), "models": dict(models)}


if __name__ == "__main__":
    out = main()
    out["dbt_build"] = run_results()
    out.update(dbt_inventory())
    save_metrics("dbt", out)
