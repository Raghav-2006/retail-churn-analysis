"""Validate the tiering model on future revenue, compare it with baselines and a
learned model, test weight sensitivity, and draw the charts.

    python -m tiering.validate      # writes metrics/tiering.json and figures/04-05

Scoring date 2011-06-01; outcome = revenue from 2011-06-01 to 2011-11-30.

The learned model must not be fit on the outcome it is judged on, so it is
trained one period earlier: features at 2010-12-01 -> revenue 2010-12-01 to
2011-05-31 (same 6-month horizon), then applied unchanged at 2011-06-01.
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.metrics import save_metrics
from tiering.features import CUTOFF, FEATURES, OUTCOME_END, build_dataset, future_revenue
from tiering.score import TIERS, WEIGHTS, explain, percentiles, score_customers

TRAIN_CUTOFF = "2010-12-01"
TRAIN_OUTCOME_END = CUTOFF
HAND_FEATURES = [f for f, w in WEIGHTS.items() if w > 0]


def capture(score: pd.Series, outcome: pd.Series, top: float) -> float:
    """Share of total future revenue earned by the top `top` fraction of customers ranked by score."""
    n = int(np.floor(top * len(score) + 1e-9))  # same count as the tier cut-offs (490 of 4,908 for 10%)
    # sort by score, breaking ties by customer_id so every method is ranked deterministically
    order = score.to_frame("s").rename_axis("cid").reset_index().sort_values(["s", "cid"], ascending=[False, True])
    top_ids = order["cid"].iloc[:n]
    return float(outcome.loc[top_ids].sum() / outcome.sum())


def capture_curve(score: pd.Series, outcome: pd.Series, points: int = 100) -> pd.DataFrame:
    fracs = np.linspace(0, 1, points + 1)
    return pd.DataFrame({"pct_customers": 100 * fracs,
                         "pct_revenue": [100 * capture(score, outcome, f) if f > 0 else 0.0 for f in fracs]})


def ranking_metrics(score: pd.Series, outcome: pd.Series) -> dict:
    return {
        "spearman": round(float(spearmanr(score, outcome).statistic), 4),
        "capture_top10_pct": round(100 * capture(score, outcome, 0.10), 2),
        "capture_top20_pct": round(100 * capture(score, outcome, 0.20), 2),
        "capture_top30_pct": round(100 * capture(score, outcome, 0.30), 2),
    }


def tier_table(scored: pd.DataFrame, outcome: pd.Series) -> pd.DataFrame:
    df = scored[["tier"]].assign(future_revenue=outcome, active=outcome > 0)
    t = df.groupby("tier").agg(customers=("future_revenue", "size"), mean=("future_revenue", "mean"),
                               median=("future_revenue", "median"), total=("future_revenue", "sum"),
                               pct_active=("active", "mean")).reindex(TIERS)
    t["share_pct"] = 100 * t["total"] / t["total"].sum()
    t["pct_active"] = 100 * t["pct_active"]
    return t.round(2)


def learned_models(train: pd.DataFrame) -> dict:
    """Fit data-driven alternatives on the earlier period. Target: top 20% of future revenue."""
    y = (train["future_revenue"] >= train["future_revenue"].quantile(0.80)).astype(int)
    # Logistic regression on the same 0-1 percentile inputs as the hand score, so its
    # coefficients are directly comparable to hand weights ("learned weights")
    X_pct = percentiles(train[FEATURES])
    logit = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(X_pct, y)
    # Gradient boosting on raw features (handles NaN regularity natively) as a flexible upper bound
    hgb = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                         random_state=42).fit(train[FEATURES], y)
    return {"logit": logit, "hgb": hgb, "train_positive_rate": float(y.mean())}


def weight_sensitivity(features: pd.DataFrame, outcome: pd.Series, base_tiers: pd.Series) -> list[dict]:
    rows = []
    for f in HAND_FEATURES:
        for factor in (0.5, 1.5):
            w = dict(WEIGHTS)
            w[f] = WEIGHTS[f] * factor
            s = score_customers(features, w)
            changed = int((s["tier"] != base_tiers).sum())
            rows.append({"feature": f, "factor": factor, "weight": round(w[f] / sum(w.values()), 3),
                         "customers_changing_tier": changed,
                         "pct_changing_tier": round(100 * changed / len(s), 2),
                         "tier1_changes": int(((s["tier"] == "Tier 1") != (base_tiers == "Tier 1")).sum()),
                         "capture_top10_pct": round(100 * capture(s["score"], outcome, 0.10), 2)})
    return rows


def rounded_weights(weights: dict, step: float = 0.05) -> dict:
    w = {f: round(v / step) * step for f, v in weights.items()}
    total = sum(w.values())
    return {f: round(v / total, 3) for f, v in w.items()}


def pick_examples(scored: pd.DataFrame, outcome: pd.Series) -> list[int]:
    """Two contrasting, deterministic worked examples: a typical Tier 1 customer, and the
    highest-revenue customer who still lands in Tier 3 or 4 (to show why)."""
    t1 = scored[scored["tier"] == "Tier 1"]
    typical_t1 = int((t1["score"] - t1["score"].median()).abs().sort_values(kind="stable").index[0])
    low = scored[scored["tier"].isin(["Tier 3", "Tier 4"])]
    big_low = int(low["monetary"].sort_values(ascending=False, kind="stable").index[0])
    return [typical_t1, big_low]


def charts(tiers: pd.DataFrame, curves: dict[str, pd.DataFrame], mono_share: float, hand_share: float) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mtick

    from src.style import AQUA, BLUE, GRAY, INK_2, ORANGE, apply_style, save

    apply_style()
    fig, ax = plt.subplots()
    bars = ax.bar(tiers.index, tiers["mean"], color=[BLUE, BLUE, GRAY, GRAY])
    for bar, (_, r) in zip(bars, tiers.iterrows()):
        ax.annotate(f"£{r['mean']:,.0f}\n{r['share_pct']:.0f}% of revenue", (bar.get_x() + bar.get_width() / 2, r["mean"]),
                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=9, color=INK_2)
    t1 = tiers.loc["Tier 1"]
    ax.set_title(f"Tier 1 (top 10% by score) earned {t1['share_pct']:.0f}% of next-6-month revenue")
    ax.set_ylabel("Mean revenue per customer, Jun–Nov 2011 (£)")
    ax.set_xlabel("Tier assigned on 1 Jun 2011")
    ax.yaxis.set_major_formatter(mtick.StrMethodFormatter("£{x:,.0f}"))
    ax.set_ylim(0, tiers["mean"].max() * 1.25)
    save(fig, "04_future_revenue_by_tier.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))
    styles = {"Hand-weighted score": (BLUE, "-"), "Hand-weighted v2 (learned weights, rounded)": (BLUE, ":"),
              "Monetary only": (ORANGE, "-"), "Equal weights": (AQUA, "--")}
    for name, (color, ls) in styles.items():
        c = curves[name]
        ax.plot(c["pct_customers"], c["pct_revenue"], color=color, linestyle=ls, label=name)
    ax.plot([0, 100], [0, 100], color=GRAY, linewidth=1, linestyle="--", label="Random")
    ax.axvline(10, color=GRAY, linewidth=0.8)
    ax.set_title(f"Top 10% by score capture {hand_share:.0f}% of next-period revenue "
                 f"(monetary only: {mono_share:.0f}%)")
    ax.set_xlabel("Customers targeted, ranked by score on 1 Jun 2011 (%)")
    ax.set_ylabel("Share of Jun–Nov 2011 revenue captured (%)")
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.grid(axis="x")
    ax.legend(loc="lower right")
    save(fig, "05_capture_curve.png")
    plt.close(fig)


def main() -> dict:
    data = build_dataset(CUTOFF, OUTCOME_END)
    features, outcome = data[FEATURES], data["future_revenue"]
    scored = score_customers(features)

    # Context: revenue in the outcome window from customers we could not score (first seen after cutoff)
    all_future = future_revenue(CUTOFF, OUTCOME_END)
    new_customer_share = 100 * (1 - outcome.sum() / all_future.sum())

    tiers = tier_table(scored, outcome)
    monotonic_mean = bool(tiers["mean"].is_monotonic_decreasing)
    monotonic_median = bool(tiers["median"].is_monotonic_decreasing)

    # Baselines and alternatives, all scored on the same customers at the same cutoff
    equal = {f: (1.0 if f in HAND_FEATURES else 0.0) for f in FEATURES}
    train = build_dataset(TRAIN_CUTOFF, TRAIN_OUTCOME_END)
    models = learned_models(train)
    methods = {
        "Hand-weighted score": scored["score"],
        "Monetary only": features["monetary"],
        "Equal weights": score_customers(features, equal)["score"],
        "Logistic regression (learned)": pd.Series(
            models["logit"].predict_proba(percentiles(features))[:, 1], index=features.index),
        "Gradient boosting (learned)": pd.Series(
            models["hgb"].predict_proba(features)[:, 1], index=features.index),
    }
    comparison = [{"method": name, **ranking_metrics(s, outcome)} for name, s in methods.items()]
    curves = {name: capture_curve(s, outcome) for name, s in methods.items()}

    coefs = models["logit"][-1].coef_[0]
    pos = np.clip(coefs, 0, None)
    learned_weights = [{"feature": f, "coefficient": round(float(c), 3),
                        "implied_weight": round(float(p / pos.sum()), 3), "hand_weight": WEIGHTS[f]}
                       for f, c, p in zip(FEATURES, coefs, pos)]
    # v2: the learned weights rounded to 0.05, so the score stays a simple, explainable weighted sum.
    # They come from the 2010-12 training period only, so judging them on 2011-06..11 is still out of sample.
    v2_weights = rounded_weights({r["feature"]: r["implied_weight"] for r in learned_weights})
    v2 = score_customers(features, v2_weights)
    methods["Hand-weighted v2 (learned weights, rounded)"] = v2["score"]
    comparison.append({"method": "Hand-weighted v2 (learned weights, rounded)", **ranking_metrics(v2["score"], outcome)})
    curves["Hand-weighted v2 (learned weights, rounded)"] = capture_curve(v2["score"], outcome)
    tiers_v2 = tier_table(v2, outcome)

    sensitivity = weight_sensitivity(features, outcome, scored["tier"])
    examples = [explain(cid, scored) for cid in pick_examples(scored, outcome)]
    for ex in examples:
        ex["future_revenue"] = round(float(outcome.loc[ex["customer_id"]]), 2)

    comp = {c["method"]: c for c in comparison}
    charts(tiers, curves, comp["Monetary only"]["capture_top10_pct"], comp["Hand-weighted score"]["capture_top10_pct"])

    best = max(comparison, key=lambda c: c["capture_top10_pct"])
    metrics = {
        "cutoff": CUTOFF,
        "outcome_window": f"{CUTOFF} to 2011-11-30",
        "train_cutoff": TRAIN_CUTOFF,
        "customers_scored": len(features),
        "customers_scored_train": len(train),
        "train_positive_rate": round(models["train_positive_rate"], 4),
        "pct_scored_customers_active_in_outcome": round(100 * float((outcome > 0).mean()), 2),
        "outcome_revenue_scored_customers": round(float(outcome.sum()), 2),
        "outcome_revenue_new_customers_pct": round(float(new_customer_share), 2),
        "weights": WEIGHTS,
        "tiers": tiers.reset_index().to_dict(orient="records"),
        "monotonic_mean": monotonic_mean,
        "monotonic_median": monotonic_median,
        "comparison": comparison,
        "best_capture_top10_method": best["method"],
        "learned_weights": learned_weights,
        "v2_weights": v2_weights,
        "tiers_v2": tiers_v2.reset_index().to_dict(orient="records"),
        "monotonic_mean_v2": bool(tiers_v2["mean"].is_monotonic_decreasing),
        "sensitivity": sensitivity,
        "sensitivity_max_pct_changing": max(r["pct_changing_tier"] for r in sensitivity),
        "sensitivity_capture_range": [min(r["capture_top10_pct"] for r in sensitivity),
                                      max(r["capture_top10_pct"] for r in sensitivity)],
        "examples": examples,
    }
    save_metrics("tiering", metrics)
    print(tiers.to_string())
    print(pd.DataFrame(comparison).to_string(index=False))
    print(pd.DataFrame(learned_weights).to_string(index=False))
    print("v2 weights:", v2_weights)
    print(tiers_v2.to_string())
    print(pd.DataFrame(sensitivity).to_string(index=False))
    for ex in examples:
        print("\n" + ex["text"] + f"\n  -> actual Jun–Nov 2011 revenue: £{ex['future_revenue']:,.2f}")
    return metrics


if __name__ == "__main__":
    main()
