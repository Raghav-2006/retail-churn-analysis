"""Multi-factor customer score, tiers, and per-customer explanations.

score = sum_i  weight_i * percentile_rank_i          (weights sum to 1, score in 0..1)

Each feature is turned into a 0-1 percentile rank across customers, so a
weight means the same thing for every feature regardless of units (days,
orders, pounds). Features where lower is better (recency, cancellation rate,
irregular ordering) are inverted, so 1.0 is always "best".
"""
import pandas as pd

from tiering.features import FEATURES

# Documented starting weights (spec): R 0.25, F 0.25, M 0.30, breadth 0.10, cancellations 0.10.
# Tenure, average order value and regularity are computed and shown in explanations,
# but carry zero weight in v1; the learned model in validate.py tests whether they add signal.
WEIGHTS = {
    "recency": 0.25,
    "frequency": 0.25,
    "monetary": 0.30,
    "tenure": 0.0,
    "avg_order_value": 0.0,
    "breadth": 0.10,
    "cancel_rate": 0.10,
    "regularity": 0.0,
}
LOWER_IS_BETTER = {"recency", "cancel_rate", "regularity"}

# Tier cut-offs by score rank: top 10% / next 20% / next 30% / bottom 40%
TIER_SHARES = [("Tier 1", 0.10), ("Tier 2", 0.20), ("Tier 3", 0.30), ("Tier 4", 0.40)]
TIERS = [t for t, _ in TIER_SHARES]

LABELS = {
    "recency": "Days since last order",
    "frequency": "Orders",
    "monetary": "Revenue to date (£)",
    "tenure": "Days since first order",
    "avg_order_value": "Average order value (£)",
    "breadth": "Distinct products bought",
    "cancel_rate": "Share of orders cancelled",
    "regularity": "Std dev of days between orders",
}


def percentiles(features: pd.DataFrame) -> pd.DataFrame:
    """0-1 percentile rank per feature, oriented so that 1 is best.

    Missing regularity (fewer than 3 orders, so no spread to measure) ranks as
    the least regular: we have no evidence of a steady buying rhythm.
    """
    out = {}
    for col in FEATURES:
        higher_better = col not in LOWER_IS_BETTER
        pct = features[col].rank(pct=True, ascending=higher_better, method="average", na_option="keep")
        out[col] = pct.fillna(0.0)
    return pd.DataFrame(out, index=features.index)


def assign_tiers(score: pd.Series) -> pd.Series:
    """Tier by score rank; ties broken by customer_id so the assignment is deterministic."""
    order = score.to_frame("s").rename_axis("cid").reset_index().sort_values(["s", "cid"], ascending=[False, True])
    rank_pct = pd.Series(range(1, len(order) + 1), index=order["cid"].to_numpy()) / len(order)
    tiers = pd.Series(TIERS[-1], index=rank_pct.index)
    upper = 0.0
    for tier, share in TIER_SHARES:
        lower, upper = upper, upper + share
        tiers[(rank_pct > lower) & (rank_pct <= upper + 1e-12)] = tier
    return tiers.reindex(score.index)


def score_customers(features: pd.DataFrame, weights: dict = WEIGHTS) -> pd.DataFrame:
    """Percentiles, per-feature contributions, total score and tier for every customer."""
    total = sum(weights.values())
    w = {f: weights.get(f, 0.0) / total for f in FEATURES}
    pct = percentiles(features)
    contrib = pct.mul(pd.Series(w))
    out = pd.concat([features, pct.add_prefix("pct_"), contrib.add_prefix("contrib_")], axis=1)
    out["score"] = contrib.sum(axis=1)
    out["tier"] = assign_tiers(out["score"])
    return out


def explain(customer_id: int, scored: pd.DataFrame | None = None, weights: dict = WEIGHTS) -> dict:
    """Why is this customer in this tier? Returns the breakdown and a readable text version.

    Each row is one input: the raw value, how it ranks against all customers
    (percentile, 100 = best), its weight, and the points it adds to the 0-100 score.
    """
    if scored is None:
        from tiering.features import build_features

        scored = score_customers(build_features(), weights)
    if customer_id not in scored.index:
        raise KeyError(f"customer {customer_id} has no purchases before the scoring cutoff")
    row = scored.loc[customer_id]
    total_w = sum(weights.values())
    rank = int((scored["score"] > row["score"]).sum()) + 1

    rows = []
    for f in FEATURES:
        rows.append({
            "feature": f,
            "label": LABELS[f],
            "value": None if pd.isna(row[f]) else float(row[f]),
            "percentile": round(100 * float(row[f"pct_{f}"]), 1),
            "weight": round(weights.get(f, 0.0) / total_w, 3),
            "points": round(100 * float(row[f"contrib_{f}"]), 1),
        })
    breakdown = pd.DataFrame(rows).sort_values("points", ascending=False)

    lines = [f"Customer {customer_id}: {row['tier']}, score {100 * row['score']:.1f}/100 "
             f"(rank {rank:,} of {len(scored):,})"]
    for r in breakdown.itertuples():
        value = "n/a (fewer than 3 orders)" if r.value is None else f"{r.value:,.2f}".rstrip("0").rstrip(".")
        if r.weight > 0:
            lines.append(f"  {r.label:<32} {value:>14}  percentile {r.percentile:5.1f}"
                         f"  x weight {r.weight:.2f}  = {r.points:5.1f} pts")
        else:
            lines.append(f"  {r.label:<32} {value:>14}  percentile {r.percentile:5.1f}"
                         f"  (not weighted)")
    return {
        "customer_id": int(customer_id),
        "tier": row["tier"],
        "score": round(100 * float(row["score"]), 1),
        "rank": rank,
        "customers": len(scored),
        "breakdown": breakdown.to_dict(orient="records"),
        "text": "\n".join(lines),
    }
