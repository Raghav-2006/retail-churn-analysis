"""Parallel-run parity check: legacy job vs the Airflow DAG, before decommissioning the legacy job.

    python -m migration.parity_check                                  # clean run: expect 100% parity
    python -m migration.parity_check --inject-bug drop_country:Norway # silent-failure demo

1. Legacy: `python -m pipeline.run --schema legacy_retail` (the v1 script, unchanged defaults)
   plus v1 tiering (the v1 feature SQL over the legacy tables).
2. Orchestrated: the Airflow DAG (`airflow dags test retail_pipeline`) into orch_retail /
   orch_analytics, optionally with an injected transform bug.
3. Compare, table by table and customer by customer:
     row counts            5 warehouse tables
     column checksums      md5 of every column's values in key order (catches changed values,
                           not just changed counts)
     revenue totals        sales and cancellations, exact NUMERIC equality
     tier assignments      every customer's tier, legacy vs orchestrated
   plus a drill-down of row counts by country, so a mismatch comes with a likely cause.
4. Write migration/reports/parity_<scenario>.{json,md} and metrics/parity_<scenario>.json.
   Any mismatch writes a structured alert to logs/alerts.jsonl and exits 1.

The point: the orchestrated run's own checks (17 quality checks, 63 dbt tests) compare the
warehouse with *its own* transform output, so a bug inside the transform passes all of them.
Only a comparison against an independent reference (the legacy job) can see it.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from pipeline.alerts import write_alert
from pipeline.db import connect
from pipeline.load import WAREHOUSE_TABLES
from pipeline.quality import PRIMARY_KEYS
from src.metrics import save_metrics

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "migration" / "reports"
LEGACY_SCHEMA = "legacy_retail"


# ---- the two runs --------------------------------------------------------------------------
def run_legacy(schema: str = LEGACY_SCHEMA) -> tuple[int, pd.DataFrame]:
    proc = subprocess.run([sys.executable, "-m", "pipeline.run", "--schema", schema, "--no-artifacts"],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
    from tiering.features import build_features_legacy
    from tiering.score import score_customers

    tiers = score_customers(build_features_legacy("2011-06-01", schema=schema))[["score", "tier"]]
    return proc.returncode, tiers


def run_orchestrated(run_name: str, schema: str, analytics_schema: str, inject_bug: str) -> tuple[int, pd.DataFrame]:
    from orchestration.run_dag import run

    code = run(run_name, schema, analytics_schema, inject_bug)
    path = ROOT / "data" / "runs" / run_name / "customer_tiers.csv"
    tiers = pd.read_csv(path, index_col="customer_id", dtype={"score": str}) if path.exists() else pd.DataFrame(
        columns=["score", "tier"])
    if len(tiers):
        tiers["score"] = tiers["score"].map(float)
    return code, tiers


# ---- comparisons -----------------------------------------------------------------------------
def columns(conn, schema: str, table: str) -> list[str]:
    rows = conn.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""",
                        (schema, table)).fetchall()
    return [r[0] for r in rows]


def column_checksums(conn, schema: str, table: str) -> dict[str, str]:
    key = PRIMARY_KEYS[table]
    cols = columns(conn, schema, table)
    exprs = ", ".join(f"md5(coalesce(string_agg(coalesce({c}::text, '<null>'), '|' ORDER BY {key}), ''))"
                      for c in cols)
    values = conn.execute(f"SELECT {exprs} FROM {schema}.{table}").fetchone()
    return dict(zip(cols, values))


def compare_warehouses(legacy: str, orch: str) -> dict:
    checks = []
    with connect(schema=legacy) as conn:
        for t in WAREHOUSE_TABLES:
            n_l = conn.execute(f"SELECT count(*) FROM {legacy}.{t}").fetchone()[0]
            n_o = conn.execute(f"SELECT count(*) FROM {orch}.{t}").fetchone()[0]
            checks.append({"kind": "row_count", "name": t, "legacy": n_l, "orchestrated": n_o, "match": n_l == n_o})
        for t in WAREHOUSE_TABLES:
            cl, co = column_checksums(conn, legacy, t), column_checksums(conn, orch, t)
            for c in sorted(set(cl) | set(co)):
                checks.append({"kind": "column_checksum", "name": f"{t}.{c}", "legacy": cl.get(c),
                               "orchestrated": co.get(c), "match": cl.get(c) == co.get(c)})
        for t in ["fact_sales", "fact_cancellations"]:
            r_l = conn.execute(f"SELECT sum(revenue) FROM {legacy}.{t}").fetchone()[0]
            r_o = conn.execute(f"SELECT sum(revenue) FROM {orch}.{t}").fetchone()[0]
            checks.append({"kind": "revenue_total", "name": t, "legacy": float(r_l), "orchestrated": float(r_o),
                           "match": r_l == r_o})
        by_country = pd.DataFrame(conn.execute(f"""
            SELECT coalesce(l.country, o.country) AS country, coalesce(l.n, 0) AS legacy_rows,
                   coalesce(o.n, 0) AS orchestrated_rows
            FROM (SELECT country, count(*) AS n FROM {legacy}.fact_sales GROUP BY 1) l
            FULL JOIN (SELECT country, count(*) AS n FROM {orch}.fact_sales GROUP BY 1) o USING (country)
            WHERE coalesce(l.n, 0) <> coalesce(o.n, 0)
            ORDER BY 1""").fetchall(), columns=["country", "legacy_rows", "orchestrated_rows"])
    return {"checks": checks, "country_drilldown": by_country.to_dict(orient="records")}


def compare_tiers(legacy: pd.DataFrame, orch: pd.DataFrame) -> dict:
    ids = legacy.index.union(orch.index)
    lt, ot = legacy["tier"].reindex(ids), orch["tier"].reindex(ids)
    only_legacy = int(ot.isna().sum())
    only_orch = int(lt.isna().sum())
    changed = int(((lt != ot) & lt.notna() & ot.notna()).sum())
    moved = pd.crosstab(lt.fillna("missing"), ot.fillna("missing")) if (only_legacy + only_orch + changed) else None
    return {
        "customers_legacy": len(legacy), "customers_orchestrated": len(orch),
        "missing_in_orchestrated": only_legacy, "extra_in_orchestrated": only_orch,
        "tier_changed": changed, "tier_agreement_pct": round(100 * float((lt == ot).sum()) / len(ids), 3),
        "match": only_legacy == only_orch == changed == 0,
        "transitions": {f"{a} -> {b}": int(moved.loc[a, b]) for a in moved.index for b in moved.columns
                        if a != b and moved.loc[a, b]} if moved is not None else {},
    }


# ---- report ----------------------------------------------------------------------------------
def to_markdown(r: dict) -> str:
    bad = [c for c in r["warehouse"]["checks"] if not c["match"]]
    lines = [f"# Parity report: {r['scenario']}", "",
             f"- Injected bug: `{r['inject_bug'] or 'none'}`",
             f"- Legacy job exit code {r['legacy_exit']}; orchestrated Airflow run: **{r['orchestrated_dag_state']}** "
             f"({', '.join(f'{k} {v}' for k, v in r['orchestrated_task_states'].items())})",
             f"- The orchestrated run's own checks: {r['orchestrated_internal_checks']['quality_checks']} quality checks, "
             f"{r['orchestrated_internal_checks']['quality_failures']} failed; dbt tests {r['orchestrated_internal_checks']['dbt_tests']}",
             f"- **Parity: {r['parity_pct']}%** ({r['checks_passed']} of {r['checks_total']} checks match); "
             f"verdict: **{'PASS' if r['pass'] else 'FAIL'}**",
             f"- Tiers: {r['tiers']['tier_agreement_pct']}% of customers agree; "
             f"{r['tiers']['missing_in_orchestrated']} missing, {r['tiers']['tier_changed']} changed tier", ""]
    by_kind = {}
    for c in r["warehouse"]["checks"]:
        k = by_kind.setdefault(c["kind"], [0, 0])
        k[0] += c["match"]
        k[1] += 1
    lines += ["| check | matching | total |", "|---|---|---|"]
    lines += [f"| {k} | {v[0]} | {v[1]} |" for k, v in by_kind.items()]
    lines += [f"| tier assignments (customers) | {r['tiers']['customers_legacy'] - r['tiers']['missing_in_orchestrated'] - r['tiers']['tier_changed']} | {r['tiers']['customers_legacy']} |", ""]
    if bad:
        lines += ["**Mismatches**", "", "| kind | name | legacy | orchestrated |", "|---|---|---|---|"]
        lines += [f"| {c['kind']} | {c['name']} | {str(c['legacy'])[:18]} | {str(c['orchestrated'])[:18]} |"
                  for c in bad[:25]]
        if len(bad) > 25:
            lines.append(f"| ... | {len(bad) - 25} more | | |")
        lines.append("")
    if r["warehouse"]["country_drilldown"]:
        lines += ["**Row counts that differ, by country**", "", "| country | legacy | orchestrated |", "|---|---|---|"]
        lines += [f"| {d['country']} | {d['legacy_rows']:,} | {d['orchestrated_rows']:,} |"
                  for d in r["warehouse"]["country_drilldown"]]
        lines.append("")
    if r["tiers"]["transitions"]:
        lines += ["**Tier transitions (legacy -> orchestrated)**", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(r["tiers"]["transitions"].items(), key=lambda kv: -kv[1])]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-bug", default="")
    ap.add_argument("--scenario", default=None, help="report name (default: clean / injected)")
    a = ap.parse_args(argv)
    scenario = a.scenario or ("injected" if a.inject_bug else "clean")
    t0 = time.perf_counter()

    legacy_exit, legacy_tiers = run_legacy()
    orch_exit, orch_tiers = run_orchestrated(f"parity_{scenario}", "orch_retail", "orch_analytics", a.inject_bug)
    run_dir = ROOT / "data" / "runs" / f"parity_{scenario}"
    states = json.loads((run_dir / "airflow_states.json").read_text())
    task_log = {}
    if (run_dir / "task_log.jsonl").exists():
        for line in (run_dir / "task_log.jsonl").read_text().splitlines():
            rec = json.loads(line)
            task_log[rec["step"]] = rec
    internal = {  # what the orchestrated run's own checks concluded
        "quality_checks": task_log.get("quality", {}).get("checks"),
        "quality_warnings": task_log.get("quality", {}).get("warn"),
        "quality_failures": 0 if "quality" in task_log else None,
        "dbt_tests": task_log.get("dbt_build", {}).get("dbt", {}).get("test"),
    }
    wh = compare_warehouses(LEGACY_SCHEMA, "orch_retail")
    tiers = compare_tiers(legacy_tiers, orch_tiers)

    total = len(wh["checks"]) + 1
    passed = sum(c["match"] for c in wh["checks"]) + tiers["match"]
    report = {
        "scenario": scenario, "inject_bug": a.inject_bug,
        "legacy_exit": legacy_exit, "orchestrated_exit": orch_exit,
        "orchestrated_task_states": states["tasks"], "orchestrated_dag_state": states["dag_state"],
        "orchestrated_internal_checks": internal,
        "checks_total": total, "checks_passed": passed, "parity_pct": round(100 * passed / total, 2),
        "pass": passed == total and legacy_exit == 0 and orch_exit == 0,
        "warehouse": wh, "tiers": tiers, "seconds": round(time.perf_counter() - t0, 1),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"parity_{scenario}.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (REPORTS / f"parity_{scenario}.md").write_text(to_markdown(report))
    summary = {k: v for k, v in report.items() if k != "warehouse"}
    summary["mismatched_checks"] = [f"{c['kind']}: {c['name']}" for c in wh["checks"] if not c["match"]]
    summary["country_drilldown"] = wh["country_drilldown"]
    save_metrics(f"parity_{scenario}", summary)
    print(to_markdown(report))

    if not report["pass"]:
        write_alert("parity", "legacy_vs_orchestrated",
                    f"parity {report['parity_pct']}%: {total - passed} of {total} checks differ; "
                    f"{tiers['missing_in_orchestrated']} customers missing, {tiers['tier_changed']} changed tier",
                    {"scenario": scenario, "mismatched": summary["mismatched_checks"][:50],
                     "country_drilldown": wh["country_drilldown"]}, run_id=states["run_id"])
        print("PARITY FAILED: do not decommission the legacy job", file=sys.stderr)
        return 1
    print("PARITY OK: the orchestrated pipeline reproduces the legacy job exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
