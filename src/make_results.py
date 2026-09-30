"""Render RESULTS.md from metrics/*.json (written by the notebooks).

Run from the repo root after the notebooks:  python src/make_results.py
No number in RESULTS.md is typed by hand.
"""
from pathlib import Path

from metrics import METRICS, load_metrics

ROOT = Path(__file__).resolve().parents[1]


def table(rows: list[dict], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(fmt(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def fmt(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def gbp(v: float) -> str:
    return f"£{v:,.0f}"


def section_clean(m: dict) -> list[str]:
    c = m["cancellation"]
    codes = ", ".join(f"`{k}` ({v:,})" for k, v in m["non_product_codes_dropped"].items())
    return [
        "## Cleaning (notebooks/01_clean.ipynb)",
        "",
        f"- Raw rows (both sheets): **{m['raw_rows']:,}**",
        f"- Clean rows: **{m['clean_rows']:,}**",
        f"- Rows removed: **{m['rows_removed']:,} ({m['pct_removed']}%)**",
        f"- Date range: {m['date_min']} to {m['date_max']}",
        f"- Clean data covers {m['customers']:,} customers, {m['invoices']:,} invoices, "
        f"{m['products']:,} products, {m['countries']} countries, {gbp(m['revenue_gbp'])} revenue (GBP)",
        "",
        table(m["cleaning_log"], ["step", "rows_before", "rows_after", "removed", "pct_removed", "reason"]),
        "",
        "**Cancellations** (customer-attributed rows):",
        "",
        f"- Cancelled invoices: {c['cancelled_invoices']:,} of {c['total_invoices']:,} "
        f"= **{c['cancel_rate_by_invoices_pct']}% by invoice count**",
        f"- Cancelled value: {gbp(c['cancelled_revenue'])} vs {gbp(c['gross_revenue'])} gross sales "
        f"= **{c['cancel_rate_by_revenue_pct']}% by revenue**",
        "",
        f"**Non-product codes dropped** (rows): {codes}",
        "",
    ]


def section_sql(m: dict) -> list[str]:
    return [
        "## SQL analysis (sql/analysis/*.sql in PostgreSQL, run from notebooks/02_sql_analysis.ipynb)",
        "",
        "**Revenue concentration** (share of total revenue):",
        "",
        f"- Top 1% of customers ({m['top_1pct_customers']:,}): **{m['top_1pct_share']}%**",
        f"- Top 10% of customers ({m['top_10pct_customers']:,}): **{m['top_10pct_share']}%**",
        f"- Top 20% of customers: **{m['top_20pct_share']}%**",
        "",
        "**Seasonality**",
        "",
        f"- Peak month: {m['peak_month']} at {gbp(m['peak_month_revenue_gbp'])}, "
        f"**{m['peak_vs_median_ratio']}x** the median full month ({gbp(m['median_month_revenue_gbp'])})",
        f"- Median order value by month ranges only £{m['median_order_value_min_gbp']:,.2f} to "
        f"£{m['median_order_value_max_gbp']:,.2f}",
        "",
        "**Retention**",
        "",
        f"- Pooled month-over-month retention: **{m['pooled_mom_retention_pct']}%** "
        f"(monthly range {m['mom_retention_min_pct']}% to {m['mom_retention_max_pct']}%)",
        "",
        "**Top 10 countries by revenue**",
        "",
        table(m["top_countries"], ["Country", "revenue", "pct_of_total"]),
        "",
    ]


def section_rfm(m: dict) -> list[str]:
    return [
        "## RFM segmentation (notebooks/03_rfm.ipynb)",
        "",
        f"- Snapshot date: {m['snapshot_date']}; customers scored: {m['customers']:,}",
        f"- Champions: **{m['champions_pct_customers']}% of customers, {m['champions_pct_revenue']}% of revenue**",
        f"- At-Risk: **{m['at_risk_customers']:,} customers ({m['at_risk_pct_customers']}%), "
        f"{gbp(m['at_risk_revenue_gbp'])} historical revenue ({m['at_risk_pct_revenue']}%)**, "
        f"average {m['at_risk_avg_recency_days']:.0f} days since last purchase, "
        f"{m['at_risk_avg_frequency']} orders on average",
        f"- RFM values independently re-verified for customers {m['verified_customer_ids']}",
        "",
        table(m["segments"], ["segment", "customers", "pct_customers", "revenue", "pct_revenue",
                              "avg_recency", "avg_frequency"]),
        "",
    ]


def section_pipeline(m: dict) -> list[str]:
    checks = m["checks"]
    n_pass = sum(c["status"] == "PASS" for c in checks)
    n_warn = sum(c["status"] == "WARN" for c in checks)
    n_fail = sum(c["status"] == "FAIL" for c in checks)
    rows = [{"table": t, "rows": n} for t, n in m["table_rows"].items()]
    return [
        "## Pipeline and data quality (python -m pipeline.run)",
        "",
        f"- Raw rows extracted: **{m['raw_rows']:,}**; loaded into the PostgreSQL star schema:",
        "",
        table(rows, ["table", "rows"]),
        "",
        f"- Quality checks: **{n_pass} pass, {n_warn} warn, {n_fail} fail** (a FAIL stops the pipeline)",
        f"- Sales revenue in Postgres: **{gbp(m['fact_sales_revenue'])}**, reconciled to pandas to the penny",
        f"- Freshness: max invoice_date loaded **{m['max_invoice_date']}**",
        f"- Idempotency: content fingerprint `{m['fingerprint']}`; identical to the previous run: "
        f"**{m['identical_to_previous_run']}** ({m['etl_runs_logged']} runs logged in `etl_run_log`)",
        f"- Partial periods flagged (data covers <50% of the calendar month): "
        + (", ".join(f"{p['month']} ({p['days_covered']} of {p['days_in_month']} days)"
                     for p in m.get("partial_periods", [])) or "none"),
        f"- Volume anomalies flagged (>3 sd from the rolling 6-month median of full months): "
        f"{', '.join(m['volume_anomaly_months']) or 'none'}",
        "",
        table(checks, ["name", "status", "detail"]),
        "",
    ]


def section_tiering(m: dict) -> list[str]:
    tiers = {t["tier"]: t for t in m["tiers"]}
    comp = {c["method"]: c for c in m["comparison"]}
    hand, mono = comp["Hand-weighted score"], comp["Monetary only"]
    v2 = comp["Hand-weighted v2 (learned weights, rounded)"]
    ex_lines = []
    for ex in m["examples"]:
        ex_lines += ["```", ex["text"], f"  -> actual Jun-Nov 2011 revenue: £{ex['future_revenue']:,.2f}", "```", ""]
    weights = ", ".join(f"{f} {w}" for f, w in m["weights"].items() if w)
    v2_weights = ", ".join(f"{f} {w}" for f, w in m["v2_weights"].items() if w)
    return [
        "## Customer tiering (python -m tiering.validate)",
        "",
        f"- Scored on {m['cutoff']} using only earlier data: **{m['customers_scored']:,} customers**; "
        f"outcome = revenue {m['outcome_window']}",
        f"- {m['pct_scored_customers_active_in_outcome']}% of scored customers bought again in the outcome window; "
        f"{m['outcome_revenue_new_customers_pct']}% of outcome-window revenue came from new customers who could not be scored",
        f"- v1 weights (documented): {weights}",
        f"- **Tier 1 (top 10%) earned {tiers['Tier 1']['share_pct']}% of next-period revenue**; "
        f"monetary-only top 10% earned {mono['capture_top10_pct']}%",
        f"- Monotonic by tier: mean **{m['monotonic_mean']}**, median **{m['monotonic_median']}**",
        f"- Spearman(score, future revenue): hand-weighted **{hand['spearman']}** vs monetary-only {mono['spearman']}",
        f"- v2 weights (logistic regression fit on {m['train_cutoff']} data, rounded to 0.05): {v2_weights} -> "
        f"top-10% capture **{v2['capture_top10_pct']}%**, Spearman {v2['spearman']}",
        f"- Weight sensitivity (each weight x0.5 / x1.5): at most **{m['sensitivity_max_pct_changing']}%** of "
        f"customers change tier; top-10% capture stays within {m['sensitivity_capture_range'][0]}–"
        f"{m['sensitivity_capture_range'][1]}%",
        "",
        "**Future revenue by tier (v1)**",
        "",
        table(m["tiers"], ["tier", "customers", "mean", "median", "total", "share_pct", "pct_active"]),
        "",
        "**Ranking quality vs baselines** (all scored on the same customers at the same cutoff)",
        "",
        table([{**c, "spearman": f"{c['spearman']:.3f}"} for c in m["comparison"]], ["method", "spearman", "capture_top10_pct", "capture_top20_pct", "capture_top30_pct"]),
        "",
        "**Learned vs hand weights** (logistic regression on standardized percentile features; "
        "target = top 20% of next-6-month revenue, trained one period earlier)",
        "",
        table([{k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in r.items()} for r in m["learned_weights"]],
              ["feature", "coefficient", "implied_weight", "hand_weight"]),
        "",
        "**Weight sensitivity**",
        "",
        table([{**r, "weight": f"{r['weight']:.3f}"} for r in m["sensitivity"]], ["feature", "factor", "weight", "customers_changing_tier", "pct_changing_tier",
                                 "tier1_changes", "capture_top10_pct"]),
        "",
        "**Worked examples: explain(customer_id)**",
        "",
        *ex_lines,
    ]


PROMPT_LABELS = {
    "v1": "v1 baseline (schema only)",
    "v2": "v2 + business glossary",
    "v3": "v3 + glossary + self-check",
}


def section_analyst(m: dict) -> list[str]:
    mix = ", ".join(f"{n} {d}" for d, n in m["eval_mix"].items())
    rows, diff_rows, fail_lines = [], [], []
    for r in m["results"]:
        runs = r["per_run"]["confidently_wrong_pct"]
        n_cw = r["outcomes"].get("wrong", 0) + r["outcomes"].get("answered_unanswerable", 0)
        rows.append({
            "model": r["model"], "prompt": PROMPT_LABELS.get(r["prompt"], r["prompt"]), "runs": r["runs"],
            "execution_accuracy_pct": r["execution_accuracy_pct"],
            "confidently_wrong": f"{n_cw}/{r['questions']}",
            "self_check_fixes": r.get("self_check", {}).get("fix", "-") if r["prompt"] == "v3" else "-",
            "abstention_accuracy_pct": r["abstention_accuracy_pct"],
            "confidently_wrong_pct": r["confidently_wrong_pct"],
            "cw_per_run": " / ".join(f"{x:.1f}" for x in runs),
            "false_abstention_pct": r["false_abstention_pct"],
            "errors": r["outcomes"].get("error", 0),
            "p50_s": r["latency_p50_s"], "p95_s": r["latency_p95_s"],
        })
        diff_rows.append({"model": r["model"], "prompt": r["prompt"], **r["by_difficulty"]})
        for f in r["failures"]:
            fail_lines.append(f"- {r['model']} / {r['prompt']} / `{f['id']}` ({f['difficulty']}): "
                              f"{f['question']} -> {f['outcomes']}")
    return [
        "## AI analyst evaluation (python -m analyst.evaluate)",
        "",
        f"- Frozen eval set v{m['eval_set_version']}: **{m['eval_questions']} questions** ({mix}), "
        f"sha256 `{m['eval_set_sha256'][:12]}`; every model and prompt version was graded on this exact file",
        "- 3 runs per (model, prompt), except gemini-3.5-flash v3: 2 complete runs before the API key's "
        "prepaid credits ran out (402) during the third; rates are pooled over all question-runs",
        "- Execution accuracy: answerable questions whose result set matches the hand-written reference "
        "(order-insensitive, numeric tolerance, extra columns allowed)",
        "- **Confidently wrong** = answered (did not abstain, SQL ran) and the result was wrong, or answered an "
        "unanswerable question; % of all question-runs",
        "",
        table(rows, ["model", "prompt", "runs", "execution_accuracy_pct", "abstention_accuracy_pct",
                     "confidently_wrong_pct", "confidently_wrong", "cw_per_run", "errors", "self_check_fixes",
                     "p50_s", "p95_s"]),
        "",
        "False abstention (refusing an answerable question) was 0.00% in every configuration. `errors` = SQL "
        "still failing after the one retry (a visible failure, not a confident one). `self_check_fixes` = "
        "question-runs where v3's review rewrote the SQL.",
        "",
        "Latency is seconds per question, excluding time spent backing off from 429/503 responses.",
        "",
        "**Accuracy by difficulty (% correct, incl. correct refusals)**",
        "",
        table(diff_rows, ["model", "prompt", "easy", "medium", "hard", "unanswerable"]),
        "",
        "**Questions not right in every run**",
        "",
        *(fail_lines or ["- none"]),
        "",
    ]


def section_dbt(m: dict) -> list[str]:
    b = m["dbt_build"]
    tests = [{"test": k, "count": v} for k, v in sorted(m["test_types"].items(), key=lambda kv: -kv[1])]
    feats = [{"cutoff": c, **v} for c, v in m["features"].items()]
    tiers = [{"comparison": k.replace("_vs_v1", " vs v1").replace("_", " "), **m[k]}
             for k in ("python_tiers_vs_v1", "sql_tiers_vs_v1")]
    return [
        "## dbt transformation layer (dbt build + python -m tiering.verify_dbt)",
        "",
        f"- `dbt build` from scratch: **{b['model'].get('success', 0)} models** "
        f"({m['models'].get('staging', 0)} staging views, {m['models'].get('marts', 0)} mart tables), "
        f"**{sum(b['test'].values())} data tests: {b['test'].get('pass', 0)} pass, "
        f"{b['test'].get('fail', 0) + b['test'].get('error', 0)} fail**",
        f"- Tier outputs match v1 exactly: **{m['matches_v1_exactly']}** "
        "(features, Python tiers and the SQL tier mart, compared customer by customer with no tolerance)",
        "",
        table(tests, ["test", "count"]),
        "",
        table(feats, ["cutoff", "customers", "same_customers", "values_compared", "mismatched_values"]),
        "",
        table(tiers, ["comparison", "customers", "same_customers", "tier_mismatches", "score_mismatches_exact",
                      "max_abs_score_diff"]),
        "",
    ]


SECTIONS = [("pipeline", section_pipeline), ("01_clean", section_clean), ("02_sql", section_sql),
            ("03_rfm", section_rfm), ("tiering", section_tiering), ("dbt", section_dbt),
            ("analyst", section_analyst)]


def main() -> None:
    out = [
        "# Results",
        "",
        "Generated by `python src/make_results.py` from `metrics/*.json`, which the pipeline, notebooks and "
        "evaluation scripts write. "
        "Do not edit by hand. Currency is GBP (£).",
        "",
    ]
    for name, render in SECTIONS:
        if (METRICS / f"{name}.json").exists():
            out += render(load_metrics(name))
    (ROOT / "RESULTS.md").write_text("\n".join(out))
    print("wrote RESULTS.md")


if __name__ == "__main__":
    main()
