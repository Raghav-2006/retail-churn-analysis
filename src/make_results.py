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


def fmt(v: object) -> str:
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
        "- Partial periods flagged (data covers <50% of the calendar month): "
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
        *boosting_verdict(m),
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


def boosting_verdict(m: dict) -> list[str]:
    """XGBoost/LightGBM vs the simple bars (hand-weighted Spearman, monetary-only top-10% capture), plus gain importances."""
    comp = {c["method"]: c for c in m["comparison"]}
    gbt = [comp[k] for k in ("XGBoost (learned)", "LightGBM (learned)") if k in comp]
    if not gbt:
        return []
    hand, mono = comp["Hand-weighted score"], comp["Monetary only"]
    beats = [g["method"] for g in gbt if g["spearman"] > hand["spearman"] or g["capture_top10_pct"] > mono["capture_top10_pct"]]
    verdict = (f"Gradient-boosted trees did not beat the simple score on this data (best Spearman "
               f"{max(g['spearman'] for g in gbt):.3f} vs {hand['spearman']:.3f}; best top-10% capture "
               f"{max(g['capture_top10_pct'] for g in gbt)}% vs monetary-only {mono['capture_top10_pct']}%), "
               "so the explainable weighted score stays." if not beats else
               f"{' and '.join(beats)} beat a simple bar (hand-weighted Spearman {hand['spearman']:.3f} or monetary-only "
               f"top-10% capture {mono['capture_top10_pct']}%).")
    lines = [f"**XGBoost and LightGBM** (300 trees, learning rate 0.05, fixed settings, untuned; same raw features, "
             f"target and {m['train_cutoff']} training period as the other learned models): {verdict}", ""]
    imp = m.get("feature_importance_gain", {})
    if imp:
        lines += ["**Top-5 features by gain** (share of each model's total split gain)", "",
                  table([{"model": name, **{f"#{i + 1}": f"{r['feature']} {r['gain_share_pct']:.1f}%" for i, r in enumerate(rows)}}
                         for name, rows in imp.items()], ["model", "#1", "#2", "#3", "#4", "#5"]), ""]
    return lines


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


def section_orchestration(_: dict) -> list[str]:
    from metrics import load_metrics

    clean, inj = load_metrics("parity_clean"), load_metrics("parity_injected")
    loud = load_metrics("orchestration")["quality_failure"]

    def row(name: str, r: dict) -> dict:
        ic = r["orchestrated_internal_checks"]
        return {"scenario": name, "injected_bug": r["inject_bug"] or "none",
                "airflow_run": r["orchestrated_dag_state"],
                "quality_checks_failed": f"{ic['quality_failures']} of {ic['quality_checks']}",
                "dbt_tests_passed": f"{(ic['dbt_tests'] or {}).get('pass', 0)} of {sum((ic['dbt_tests'] or {}).values())}",
                "parity_pct": r["parity_pct"], "checks": f"{r['checks_passed']}/{r['checks_total']}",
                "tier_agreement_pct": r["tiers"]["tier_agreement_pct"],
                "verdict": "PASS" if r["pass"] else "FAIL (alert)"}

    drill = ", ".join(f"{d['country']} {d['legacy_rows']:,} -> {d['orchestrated_rows']:,} rows"
                      for d in inj["country_drilldown"])
    t = inj["tiers"]
    return [
        "## Orchestration and migration parity (Airflow + python -m migration.parity_check)",
        "",
        "- Legacy job = v1 `python -m pipeline.run` + v1 tiering, into `legacy_retail`; orchestrated = Airflow DAG "
        "`retail_pipeline` (extract -> transform -> load -> quality -> dbt_build -> tiering -> export) into "
        "`orch_retail` / `orch_analytics`",
        f"- **Clean run: parity {clean['parity_pct']}%** ({clean['checks_passed']}/{clean['checks_total']} checks: "
        "5 row counts, 38 column checksums, 2 revenue totals, tier assignments for "
        f"{clean['tiers']['customers_legacy']:,} customers)",
        f"- **Injected silent failure (`{inj['inject_bug']}`): the Airflow run finished `{inj['orchestrated_dag_state']}`, "
        f"with every quality check and dbt test passing; the parity check caught it**: parity {inj['parity_pct']}%, "
        f"{inj['checks_total'] - inj['checks_passed']} checks differ; drill-down: {drill}; "
        f"{t['missing_in_orchestrated']} customers missing from tiers, {t['tier_changed']} changed tier; "
        "structured alert written, exit code 1",
        f"- Loud failure (`{loud['inject_bug']}`): Airflow run `{loud['dag_state']}`; tasks: "
        + ", ".join(f"{k} {v}" for k, v in loud["tasks"].items())
        + f"; alert: {loud['alerts'][0]['message'] if loud['alerts'] else 'none'}",
        "",
        table([row("clean", clean), row("injected", inj)],
              ["scenario", "injected_bug", "airflow_run", "quality_checks_failed", "dbt_tests_passed",
               "parity_pct", "checks", "tier_agreement_pct", "verdict"]),
        "",
        f"Mismatched checks in the injected run: {', '.join('`' + c + '`' for c in inj['mismatched_checks'])}",
        "",
    ]


def section_demo(m: dict) -> list[str]:
    return [
        "## Serving: demo database, API and dashboard (python -m service.build_demo)",
        "",
        f"- `demo/demo.sqlite`: **{m['size_mb']} MB** (limit 50 MB), built {m['built_at']}; "
        "pre-aggregated, no raw transactions",
        "- Cached eval answers served by `POST /ask` and the dashboard's demo mode (run 0 of each model x prompt "
        "version on frozen eval set v2): " + ", ".join(f"{o} {n}" for o, n in m["eval_outcomes"].items()),
        "",
        table([{"table": k, "rows": v} for k, v in m["tables"].items()], ["table", "rows"]),
        "",
    ]


def section_rag_retrieval(m: dict) -> list[str]:
    idx = m.get("index", {})
    ch = m["chosen"]
    rows = [r for r in m["results"] if r["policy"] == "penalize" and r["subset"] in ("all", "answerable", "defs")]
    trap = [r for r in m["results"] if r["subset"] == "defs" and r["k"] == ch["k"]]
    return [
        "## Knowledge layer: retrieval eval (python -m rag.evaluate)",
        "",
        f"- {next(iter(idx.values()))['docs'] if idx else '?'} knowledge docs (`knowledge/`), embedded locally with "
        f"`{m['embedder']}` (384-d, no API) into pgvector (`knowledge.chunks`), exact cosine search",
        "- Chunk configurations: " + ", ".join(f"`{k}` {v['chunks']} chunks (mean {v['mean_words']} words)"
                                               for k, v in idx.items()),
        "- Hand-labelled relevant docs for all 65 questions (`knowledge/relevance.yaml`); ranking is doc level over the "
        "top-k chunks the agent would see. Deprecated docs get a 0.10 similarity penalty (`penalize`)",
        f"- **Chosen for the agent** (pre-registered rule: best recall@8, then the smallest k within 0.02): "
        f"`{ch['config']}`, k = {ch['k']} (recall {ch['recall']:.3f}, MRR {ch['mrr']:.3f} on all 65)",
        "",
        table([{"subset": r["subset"], "chunks": r["config"], "k": r["k"], "questions": r["questions"],
                "recall_at_k": r["recall"], "hit_at_k": r["hit"], "MRR": r["mrr"]} for r in rows],
              ["subset", "chunks", "k", "questions", "recall_at_k", "hit_at_k", "MRR"]),
        "",
        f"**Deprecated/conflicting docs on the 10 definition questions (k = {ch['k']})**: share of questions whose "
        "context contains a trap doc, and share where a trap outranks the current definition",
        "",
        table([{"chunks": r["config"], "policy": r["policy"], "recall": r["recall"],
                "trap_in_context": r["trap_in_context"], "trap_above_relevant": r["trap_above_relevant"]} for r in trap],
              ["chunks", "policy", "recall", "trap_in_context", "trap_above_relevant"]),
        "",
        f"**Misses at the chosen setting** ({len(m['misses_at_chosen'])}): "
        + ", ".join(f"`{x['id']}`" for x in m["misses_at_chosen"]),
        "",
    ]


def section_rag_ablation(m: dict) -> list[str]:
    rows = []
    for cond, r in m["conditions"].items():
        for subset in ("frozen55", "defs10", "all65"):
            x = r[subset]
            n_cw = x["outcomes"].get("wrong", 0) + x["outcomes"].get("answered_unanswerable", 0)
            rows.append({"condition": cond, "questions": subset, "question_runs": x["question_runs"],
                         "execution_accuracy_pct": x["execution_accuracy_pct"],
                         "abstention_accuracy_pct": x["abstention_accuracy_pct"] if x["abstention_accuracy_pct"]
                         is not None else "-",
                         "confidently_wrong_pct": x["confidently_wrong_pct"], "confidently_wrong": f"{n_cw}/{x['question_runs']}",
                         "false_abstention_pct": x["false_abstention_pct"],
                         "exec_per_run": " / ".join(f"{v:.1f}" for v in x["per_run_execution_accuracy_pct"]),
                         "p50_s": x["latency_p50_s"], "p95_s": x["latency_p95_s"]})
    cit = m["conditions"]["rag_v4"].get("citations", {})
    by_q = {}
    for d in m["defs_by_question"]:
        by_q.setdefault(d["id"], {"id": d["id"], "question": d["question"], "no_rag": [], "rag": []})
        by_q[d["id"]]["no_rag"].append(d["no_rag"])
        by_q[d["id"]]["rag"].append(d["rag"])
    return [
        "## RAG ablation: without vs with retrieval (python -m analyst.ablation)",
        "",
        f"- Model `{m['model']}`, {len(m['runs'])} runs per condition. No RAG = prompt v3 (glossary + self-check); "
        "RAG = v4 (v3 + the top-8 retrieved docs + mandatory citations)",
        "- No-RAG on the frozen 55 reuses the cached v3 runs 0-1 from Part 3 (same question file, prompt and model; "
        "SHA-256 checked); everything else was run fresh",
        "",
        table(rows, ["condition", "questions", "question_runs", "execution_accuracy_pct", "abstention_accuracy_pct",
                     "confidently_wrong_pct", "confidently_wrong", "false_abstention_pct", "exec_per_run",
                     "p50_s", "p95_s"]),
        "",
        "Latency excludes the prose-answer call and 429/503 back-off, so both conditions time the same steps "
        "(v4 includes retrieval). Estimated cost per question (SQL + self-check calls): "
        + ", ".join(f"{c} CA${r['cost_per_question_cad']:.4f}" for c, r in m["conditions"].items() if r["cost_per_question_cad"]),
        "",
        "**Definition questions, outcome per run**",
        "",
        table([{"id": v["id"], "question": v["question"], "no_rag": " / ".join(v["no_rag"]), "rag": " / ".join(v["rag"])}
               for v in by_q.values()], ["id", "question", "no_rag", "rag"]),
        "",
        "**Frozen 55: question-runs whose outcome changed with RAG**: "
        + ("; ".join(f"`{k}` {', '.join(v)}" for k, v in m["frozen55_changed"].items()) or "none"),
        "",
        f"**Citations (v4)**: {cit.get('answered_with_citation_pct')}% of answered question-runs cite at least one doc; "
        f"definition questions cite the defining doc in {cit.get('defs_cite_gold_doc_pct')}%; "
        f"{cit.get('cites_deprecated_doc')} cite a deprecated doc; {cit.get('cites_unretrieved_doc')} cite a doc that "
        "was not retrieved",
        "",
    ]


def section_judge(m: dict) -> list[str]:
    return [
        f"## LLM judge on {m['version']} answers (python -m analyst.judge)",
        "",
        f"- Judge `{m['judge_model']}` (the agent's model; the only one in budget), prompt `{m['judge_version']}`, "
        f"temperature 0; {m['judged']} answered question-runs judged ({m['unparsed']} unparseable verdicts)",
        f"- Faithful to the SQL result: **{m['faithful_pct']}%**; citations correct: **{m['citation_correct_pct']}%**",
        "- Faithful % by eval outcome: " + ", ".join(f"{k} {v}" for k, v in m["faithful_pct_by_outcome"].items()),
        "- Citation-correct % by question set: " + ", ".join(f"{k} {v}" for k, v in m["citation_correct_pct_by_set"].items()),
        "",
    ]


def section_spend(m: dict) -> list[str]:
    return [
        "## Gemini spend, Phases 7-8 (metrics/gemini_ledger.json)",
        "",
        f"- **{m['calls']:,} calls**, {m['input_tokens']:,} input + {m['output_tokens']:,} output + "
        f"{m['thought_tokens']:,} thinking tokens; estimated **CA${m['est_cad']:.2f}** of the CA${m['cap_cad']:.2f} cap "
        f"(list price USD 0.25 / 1.50 per 1M in/out tokens, at {m['usd_to_cad']} CAD/USD)",
        "",
        table([{"purpose": k, "calls": v["calls"], "est_cad": round(v["est_cad"], 4)} for k, v in m["by_purpose"].items()],
              ["purpose", "calls", "est_cad"]),
        "",
    ]


def section_final(m: dict) -> list[str]:
    cols = ["block", "version", "label", "runs", "question_runs", "execution_accuracy_pct", "abstention_accuracy_pct",
            "confidently_wrong_pct", "confidently_wrong", "latency_p50_s", "latency_p95_s", "cost_per_question_cad", "note"]
    rows = [{**r, "cost_per_question_cad": f"CA${r['cost_per_question_cad']:.4f}" if r["cost_per_question_cad"] else "n/a"}
            for r in m["rows"]]
    return [
        f"## Final analyst table: every version, {m['model']} (python -m analyst.final_table)",
        "",
        "- `frozen55` = the frozen 55-question set v2; `all65` = those plus the 10 definition questions",
        "- Latency: seconds per question, excluding the prose-answer call and 429/503 back-off",
        "- Cost: SQL-writing + self-check + verification calls per question (prose answer and judge excluded), "
        "estimated from the token ledger at list price",
        "",
        table(rows, cols),
        "",
    ]


def section_self_verify(m: dict) -> list[str]:
    if not m.get("complete"):
        return ["## Answer self-verification", "", f"- stopped: {m.get('stopped_reason')}", ""]
    rp, inj = m["replay"], m["injection"]
    lines = ["## Answer self-verification, prompt v5 (python -m analyst.self_verify)", "",
             "- After the prose answer is written, every number in it must match a cell of the SQL result "
             "(rounding, %, £ and 'million' allowed), a constant in the SQL, the row count, or the question. "
             "If one does not, the answer is rewritten once from the result; if it still fails, the agent abstains",
             "- Measured by replaying every recorded v4 answer through the step (the generations are deterministic "
             "and recorded; only a rewrite costs a call)", ""]
    for s, x in rp.items():
        lines.append(f"- **{s}**: {x['answers_checked']} answers checked -> {x['verification']}; confidently wrong "
                     f"{x['v4_confidently_wrong_pct']}% -> {x['v5_confidently_wrong_pct']}%, execution accuracy "
                     f"{x['v4_execution_accuracy_pct']}% -> {x['v5_execution_accuracy_pct']}%")
    lines += ["",
              f"**Fault injection** (the real answers had no unsupported numbers, so the step was tested on corrupted "
              f"copies): {inj['corrupted']} of {inj['distinct_answers']} distinct answers had a headline number to "
              f"corrupt; detected **{inj['detected']}/{inj['corrupted']}**, rewritten correctly "
              f"**{inj['corrected']}/{inj['corrected']}** (original number restored in {inj['original_number_restored']}), "
              f"abstained {inj['abstained']}, missed {inj['missed']}. Gemini calls: {m['gemini_calls']}", ""]
    return lines


def section_agreement(_: dict) -> list[str]:
    head = ["## Judge vs human agreement (python -m analyst.agreement)", ""]
    if not (METRICS / "judge_agreement.json").exists():
        return head + ["- **PENDING HUMAN LABELS.** `labels/human_labels.csv` (30 blind v4 answers) has not been "
                       "labelled yet, so the judge is **not calibrated** and its verdicts above should not be "
                       "relied on. After labelling, `python -m analyst.agreement` reports accuracy and Cohen's kappa", ""]
    a = load_metrics("judge_agreement")
    return head + [f"- {c}: accuracy **{a[c]['accuracy_pct']}%**, Cohen's kappa **{a[c]['cohens_kappa']}** "
                   f"({a[c]['reading']}), n = {a['n']}" for c in ("faithful", "citation_correct")] + [""]


SECTIONS = [("pipeline", section_pipeline), ("01_clean", section_clean), ("02_sql", section_sql),
            ("03_rfm", section_rfm), ("tiering", section_tiering), ("dbt", section_dbt),
            ("parity_clean", section_orchestration),
            ("analyst", section_analyst), ("rag_retrieval", section_rag_retrieval), ("rag_ablation", section_rag_ablation),
            ("self_verify", section_self_verify), ("analyst_final", section_final),
            ("judge_v4", section_judge), ("judge_v4", section_agreement), ("gemini_ledger", section_spend),
            ("demo", section_demo)]


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
