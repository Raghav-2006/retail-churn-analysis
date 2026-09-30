"""Build the small, committed demo database the dashboard and API run on.

    python -m service.build_demo        # needs the full pipeline + dbt build + eval cache locally

Writes demo/demo.sqlite: pre-aggregated tables only (no raw transactions), so the Streamlit
Community Cloud app and the Docker API work without Postgres, the 45 MB dataset or an API key.
Everything in it is derived from code that already ran: the warehouse, metrics/*.json and
the cached answers from the analyst evaluation runs.
"""
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "demo" / "demo.sqlite"
CACHE_DIR = ROOT / "data" / "analyst_cache"
MAX_ROWS = 20
MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
VERSIONS = ["v1", "v2", "v3"]


def _metrics(name: str) -> dict:
    return json.loads((ROOT / "metrics" / f"{name}.json").read_text())


def _jsonable(v: object) -> object:
    return float(v) if hasattr(v, "is_finite") else (v.isoformat() if hasattr(v, "isoformat") else v)


def render_answer(columns: list[str], rows: list[list], abstained: bool, reason: str, error: str | None) -> str:
    """A plain-English answer rendered from the SQL result, with no LLM (the eval runs did not narrate)."""
    if abstained:
        return f"I can't answer that from this data. {reason}".strip()
    if error:
        return "The query failed, so there is no answer."
    if not rows:
        return "The query returned no rows."
    if len(rows) == 1 and len(columns) == 1:
        v = rows[0][0]
        return f"{columns[0].replace('_', ' ')}: {v:,.2f}" if isinstance(v, float) else f"{columns[0].replace('_', ' ')}: {v}"
    if len(rows) == 1:
        return "; ".join(f"{c.replace('_', ' ')}: {v:,.2f}" if isinstance(v, float) else f"{c.replace('_', ' ')}: {v}"
                         for c, v in zip(columns, rows[0]))
    return f"{len(rows)} rows returned (see the table)."


def normalize(q: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(q.lower().split()))


def eval_answers() -> pd.DataFrame:
    """One row per (model, prompt version, question), from run 0 of the frozen eval set v2."""
    import yaml

    from analyst.agent import run_readonly
    from analyst.evaluate import eval_set_sha, slug

    items = {i["id"]: i for i in yaml.safe_load((ROOT / "analyst" / "eval_set.yaml").read_text())}
    refs = {qid: run_readonly(it["sql"], max_rows=MAX_ROWS) for qid, it in items.items() if "sql" in it}
    out = []
    for model in MODELS:
        cache = [json.loads(line) for line in (CACHE_DIR / f"{model}.jsonl").read_text().splitlines()]
        for version in VERSIONS:
            records = json.loads((ROOT / "metrics" / f"analyst_{version}_{slug(model)}_records.json").read_text())
            for r in (r for r in records if r["run"] == 0):
                # the cache entry for this exact answer: same question, version and SQL
                hit = next((c for c in cache if c["prompt_version"] == version and c["question"] == r["question"]
                            and c["sql"] == r["sql"] and c["error"] == r["error"]), None)
                if hit is None:
                    raise LookupError(f"no cached answer for {model} {version} {r['id']}")
                ref = refs.get(r["id"])
                out.append({
                    "model": model, "prompt_version": version, "qid": r["id"], "difficulty": r["difficulty"],
                    "question": r["question"], "question_norm": normalize(r["question"]),
                    "abstained": int(hit["abstained"]), "sql": hit["sql"], "error": hit["error"],
                    "columns": json.dumps(hit["columns"]), "rows": json.dumps(hit["rows"][:MAX_ROWS], default=str),
                    "row_count": len(hit["rows"]),
                    "answer": render_answer(hit["columns"], hit["rows"], hit["abstained"], hit["answer"], hit["error"]),
                    "outcome": r["outcome"], "latency_s": hit["latency_s"], "self_check": hit.get("self_check"),
                    "reference_sql": items[r["id"]].get("sql"),
                    "reference_columns": json.dumps(ref[0]) if ref else None,
                    "reference_rows": json.dumps([[_jsonable(v) for v in row] for row in ref[1]], default=str) if ref else None,
                    "eval_set_sha256": eval_set_sha(),
                })
    return pd.DataFrame(out)


def customer_scores() -> pd.DataFrame:
    from pipeline.db import read_sql
    from tiering.features import build_dataset
    from tiering.score import score_customers

    data = build_dataset()
    scored = score_customers(data.drop(columns="future_revenue"))
    scored["future_revenue"] = data["future_revenue"]
    scored["rank"] = scored["score"].rank(ascending=False, method="min").astype(int)
    country = read_sql("SELECT customer_id, country FROM dim_customer", schema="analytics").set_index("customer_id")
    scored["country"] = country["country"].reindex(scored.index)
    return scored.reset_index(names="customer_id")


def overview_tables() -> dict[str, pd.DataFrame]:
    from pipeline.db import run_sql_file

    lorenz = run_sql_file("05_lorenz_curve")
    step = max(1, len(lorenz) // 200)
    return {
        "monthly": run_sql_file("01_monthly_revenue"),
        "countries": run_sql_file("02_top_countries"),
        "lorenz": pd.concat([lorenz.iloc[::step], lorenz.iloc[[-1]]]).drop_duplicates(),
    }


def kpis() -> pd.DataFrame:
    p, c, s = _metrics("pipeline"), _metrics("01_clean"), _metrics("02_sql")
    t, d, a = _metrics("tiering"), _metrics("dbt"), _metrics("analyst")
    pc, pi = _metrics("parity_clean"), _metrics("parity_injected")
    checks = p["checks"]
    comp = {x["method"]: x for x in t["comparison"]}
    pooled = {}
    for v in ["v1", "v2", "v3"]:
        rs = [r for r in a["results"] if r["prompt"] == v]
        cw = sum(r["outcomes"].get("wrong", 0) + r["outcomes"].get("answered_unanswerable", 0) for r in rs)
        pooled[v] = (cw, sum(r["questions"] for r in rs))
    rows = [
        ("raw_rows", c["raw_rows"], "Raw rows (both sheets)"),
        ("clean_rows", c["clean_rows"], "Sales lines after cleaning"),
        ("customers", c["customers"], "Customers"),
        ("revenue_gbp", c["revenue_gbp"], "Revenue (GBP)"),
        ("top10_share_pct", s["top_10pct_share"], "Revenue from top 10% of customers (%)"),
        ("quality_pass", sum(x["status"] == "PASS" for x in checks), "Quality checks passing"),
        ("quality_warn", sum(x["status"] == "WARN" for x in checks), "Quality checks warning"),
        ("quality_fail", sum(x["status"] == "FAIL" for x in checks), "Quality checks failing"),
        ("dbt_tests_pass", d["dbt_build"]["test"].get("pass", 0), "dbt tests passing"),
        ("dbt_matches_v1", int(d["matches_v1_exactly"]), "dbt tiers match v1 exactly"),
        ("parity_clean_pct", pc["parity_pct"], "Parity, clean migration run (%)"),
        ("parity_injected_pct", pi["parity_pct"], "Parity with injected silent bug (%)"),
        ("tier1_capture_pct", comp["Hand-weighted score"]["capture_top10_pct"], "Tier 1 share of next-6-month revenue (%)"),
        ("monetary_capture_pct", comp["Monetary only"]["capture_top10_pct"], "Monetary-only top 10% share (%)"),
        ("v2_capture_pct", comp["Hand-weighted v2 (learned weights, rounded)"]["capture_top10_pct"],
         "v2 weights top 10% share (%)"),
        ("spearman_v1", comp["Hand-weighted score"]["spearman"], "Spearman, v1 score vs future revenue"),
        *[(f"cw_{v}_pct", round(100 * n / d_, 2), f"Confidently wrong, prompt {v} (%, pooled over 3 models)")
          for v, (n, d_) in pooled.items()],
        *[(f"cw_{v}_count", f"{n}/{d_}", f"Confidently wrong answers, prompt {v}") for v, (n, d_) in pooled.items()],
    ]
    return pd.DataFrame(rows, columns=["key", "value", "label"]).astype({"value": str})


def main(path: Path = DEMO_DB) -> Path:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    t = _metrics("tiering")
    tables = {
        **overview_tables(),
        "kpis": kpis(),
        "customer_scores": customer_scores(),
        "tier_summary": pd.DataFrame(t["tiers"]),
        "tier_methods": pd.DataFrame(t["comparison"]),
        "quality_checks": pd.DataFrame(_metrics("pipeline")["checks"]),
        "eval_answers": eval_answers(),
        "meta": pd.DataFrame([{"built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                               "pipeline_fingerprint": _metrics("pipeline")["fingerprint"],
                               "scoring_cutoff": t["cutoff"], "outcome_window": t["outcome_window"],
                               "note": "Pre-aggregated demo data; no raw transactions."}]),
    }
    with sqlite3.connect(tmp) as con:
        for name, df in tables.items():
            df = df.copy()
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].map(lambda v: _jsonable(v) if v is not None else None)
            df.to_sql(name, con, index=False)
        con.execute("CREATE INDEX ix_scores_customer ON customer_scores (customer_id)")
        con.execute("CREATE INDEX ix_eval_question ON eval_answers (question_norm, model, prompt_version)")
        con.execute("VACUUM")
    tmp.replace(path)
    from src.metrics import save_metrics

    save_metrics("demo", {
        "size_mb": round(path.stat().st_size / 1e6, 2), "built_at": tables["meta"]["built_at"].iloc[0],
        "tables": {k: len(v) for k, v in tables.items()},
        "eval_outcomes": tables["eval_answers"]["outcome"].value_counts().to_dict(),
    })
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB): "
          + ", ".join(f"{k} {len(v):,}" for k, v in tables.items()))
    return path


if __name__ == "__main__":
    main()
