"""The orchestrated pipeline, one command per task (called by the Airflow DAG).

    python -m pipeline.steps <step> --run-dir data/runs/<name> --schema <warehouse> --analytics-schema <dbt>

Steps: extract -> transform -> load -> quality -> dbt_build -> tiering -> export.
Tasks hand data to each other through files in --run-dir (raw.parquet, sales.parquet, ...),
so each one can be retried on its own, and they reuse the v1 functions unchanged: the
orchestrated job must produce exactly what the legacy `python -m pipeline.run` produces,
which is what migration/parity_check.py verifies.

Every failure mode ends in pipeline.alerts.alert_and_fail(): a structured line in
logs/alerts.jsonl, then a non-zero exit so Airflow fails the task and skips the rest.

--inject-bug drop_country:<Country> exists only to demonstrate the parity check: it
simulates a transform regression that silently drops one country's rows.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from pipeline import quality
from pipeline.alerts import AlertError, alert_and_fail
from pipeline.db import connect, read_sql
from pipeline.extract import extract
from pipeline.load import build_star, fingerprint, load, table_counts
from pipeline.transform import clean

STEPS = ["extract", "transform", "load", "quality", "dbt_build", "tiering", "export"]


def inject(sales: pd.DataFrame, bug: str | None) -> pd.DataFrame:
    """Simulated regressions for the demos.

    drop_country:Norway  removes Norway's lines. SILENT: every internal check still passes.
    negative_price:10    makes the first 10 prices negative. LOUD: a quality check must fail.
    """
    if not bug:
        return sales
    kind, _, arg = bug.partition(":")
    if kind == "drop_country":
        return sales[sales["Country"] != arg].reset_index(drop=True)
    if kind == "negative_price":
        sales = sales.copy()
        sales.loc[sales.index[: int(arg)], "Price"] *= -1
        return sales
    raise ValueError(f"unknown bug {bug!r}")


def step_extract(a: argparse.Namespace) -> dict:
    raw = extract()
    raw.to_parquet(a.run_dir / "raw.parquet", index=False)
    return {"raw_rows": len(raw)}


def step_transform(a: argparse.Namespace) -> dict:
    raw = pd.read_parquet(a.run_dir / "raw.parquet")
    sales, cancels, log_df, _ = clean(raw)
    sales = inject(sales, a.inject_bug)
    sales.to_parquet(a.run_dir / "sales.parquet", index=False)
    cancels.to_parquet(a.run_dir / "cancellations.parquet", index=False)
    log_df.to_csv(a.run_dir / "cleaning_log.csv", index=False)
    return {"sales_rows": len(sales), "cancellation_rows": len(cancels), "injected_bug": a.inject_bug}


def _frames(a: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = pd.read_parquet(a.run_dir / "raw.parquet")
    sales = pd.read_parquet(a.run_dir / "sales.parquet")
    cancels = pd.read_parquet(a.run_dir / "cancellations.parquet")
    return raw, sales, cancels


def step_load(a: argparse.Namespace) -> dict:
    _, sales, cancels = _frames(a)
    with connect(schema=a.schema) as conn:
        load(conn, build_star(sales, cancels))
        return {"table_rows": table_counts(conn)}


def step_quality(a: argparse.Namespace) -> dict:
    raw, sales, cancels = _frames(a)
    with connect(schema=a.schema) as conn:
        checks, extra = quality.run_checks(conn, raw, sales, cancels, build_star(sales, cancels))
    print(quality.report(checks))
    failed = [c for c in checks if c.status == "FAIL"]
    if failed:
        alert_and_fail("quality", "data_quality", f"{len(failed)} quality check(s) failed",
                       {"failed": quality.to_records(failed)})
    return {"checks": len(checks), "warn": sum(c.status == "WARN" for c in checks),
            "fact_sales_revenue": extra["fact_sales_revenue"]}


def step_dbt_build(a: argparse.Namespace) -> dict:
    from pipeline.dbt_runner import run_dbt, run_results

    proc = run_dbt(["build"], source_schema=a.schema, target_schema=a.analytics_schema, check=False)
    counts = run_results()
    if proc.returncode != 0:
        alert_and_fail("dbt", "dbt_build", "dbt build failed (a model or a dbt test)", {"results": counts})
    return {"dbt": counts}


def step_tiering(a: argparse.Namespace) -> dict:
    from tiering.features import build_features
    from tiering.score import score_customers

    scored = score_customers(build_features("2011-06-01", schema=a.analytics_schema))
    sql = read_sql("SELECT customer_id, score, tier FROM mart_customer_tiers",
                   schema=a.analytics_schema).set_index("customer_id")
    diff = int((sql["tier"] != scored["tier"].reindex(sql.index)).sum()) + abs(len(sql) - len(scored))
    if diff:
        alert_and_fail("tiering", "python_vs_sql_tiers", f"{diff} customers differ between Python and dbt tiers")
    out = scored[["score", "tier"]].copy()
    out["score"] = out["score"].map(lambda x: repr(float(x)))
    out.to_csv(a.run_dir / "customer_tiers.csv")
    return {"customers": len(out), "tiers": out["tier"].value_counts().sort_index().to_dict()}


def step_export(a: argparse.Namespace) -> dict:
    """Publish the run: a summary other systems (and the parity check) can read."""
    with connect(schema=a.schema) as conn:
        summary = {
            "schema": a.schema,
            "analytics_schema": a.analytics_schema,
            "table_rows": table_counts(conn),
            "fingerprint": fingerprint(conn),
            "fact_sales_revenue": float(conn.execute("SELECT sum(revenue) FROM fact_sales").fetchone()[0]),
        }
    (a.run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=STEPS)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--schema", default="orch_retail")
    ap.add_argument("--analytics-schema", default="orch_analytics")
    ap.add_argument("--inject-bug", default="")
    a = ap.parse_args(argv)
    a.run_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        info = globals()[f"step_{a.step}"](a)
    except AlertError as err:
        print(f"TASK FAILED: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # anything unexpected is also an alert, never a silent pass
        try:
            alert_and_fail(a.step, "task_error", f"{type(err).__name__}: {err}")
        except AlertError:
            pass
        raise
    info["seconds"] = round(time.perf_counter() - t0, 1)
    with (a.run_dir / "task_log.jsonl").open("a") as f:
        f.write(json.dumps({"step": a.step, **info}, default=str) + "\n")
    print(json.dumps({"step": a.step, **info}, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
