"""One command for the whole pipeline:  python -m pipeline.run

extract -> transform -> load (truncate-and-load, one transaction) -> quality checks.
Exits non-zero if any quality check fails. Safe to run any number of times.

Since Phase 6 this script is the "legacy job": the Airflow DAG (orchestration/) replaces it,
and migration/parity_check.py runs both into separate schemas and compares them. The two
options below exist only for that parity run; the defaults behave exactly as in v1.

    python -m pipeline.run --schema legacy_retail --no-artifacts
"""
import argparse
import sys
import time

from pipeline import quality
from pipeline.db import SCHEMA, connect
from pipeline.extract import DATA, describe, extract
from pipeline.load import build_star, grant_readonly, load, previous_fingerprints, table_counts
from pipeline.transform import clean
from src.metrics import save_metrics


def main(schema: str = SCHEMA, save_artifacts: bool = True) -> int:
    t0 = time.perf_counter()
    raw = extract()
    describe(raw)

    sales, cancels, log_df, info = clean(raw)
    print("\ncleaning log:\n" + log_df.to_string(index=False))
    if save_artifacts:
        # The parquet copies feed the exploration notebooks (RFM)
        sales.to_parquet(DATA / "clean.parquet", index=False)
        cancels.to_parquet(DATA / "cancellations.parquet", index=False)

    tables = build_star(sales, cancels)
    with connect(schema=schema) as conn:
        loaded = load(conn, tables)
        grant_readonly(conn, schema=schema)
        counts = table_counts(conn)
        print("\nloaded:", {k: f"{v:,}" for k, v in counts.items()})
        checks, extra = quality.run_checks(conn, raw, sales, cancels, tables)
        n_runs = conn.execute("SELECT count(*) FROM etl_run_log").fetchone()[0]
        # Idempotency evidence: this run's content fingerprint vs the previous run's
        latest = previous_fingerprints(conn)
        same_as_previous = len(latest) == 2 and latest[0] == latest[1]

    print("\nquality checks:\n" + quality.report(checks))
    if not save_artifacts:
        print(f"content fingerprint {latest[0]}")
        quality.assert_all_pass(checks)
        return 0
    save_metrics("pipeline", {
        "raw_rows": len(raw),
        "table_rows": counts,
        "expected_rows": loaded,
        "checks": quality.to_records(checks),
        "etl_runs_logged": n_runs,
        "fingerprint": latest[0],
        "identical_to_previous_run": same_as_previous,
        "seconds": round(time.perf_counter() - t0, 1),
        **extra,
    })
    print(f"content fingerprint {latest[0]}; identical to previous run: {same_as_previous}")
    quality.assert_all_pass(checks)
    n_warn = sum(c.status == "WARN" for c in checks)
    print(f"\nall {len(checks)} checks passed ({n_warn} warning(s)) in {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default=SCHEMA, help="warehouse schema to load (default: retail)")
    ap.add_argument("--no-artifacts", action="store_true",
                    help="don't write data/*.parquet or metrics/pipeline.json (used by the parity check)")
    args = ap.parse_args()
    try:
        sys.exit(main(args.schema, save_artifacts=not args.no_artifacts))
    except quality.QualityCheckError as err:
        print(f"\nPIPELINE FAILED\n{err}", file=sys.stderr)
        sys.exit(1)
