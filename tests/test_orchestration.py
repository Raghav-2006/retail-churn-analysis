"""Phase 6: alerts, orchestrated steps, DAG shape and the parity comparison (no Airflow needed)."""
import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from migration.parity_check import compare_tiers, compare_warehouses
from pipeline import alerts
from pipeline.db import connect
from pipeline.load import build_star, load
from pipeline.steps import STEPS, inject
from pipeline.steps import main as step
from pipeline.transform import clean

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alert_file(tmp_path, monkeypatch):
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(alerts, "ALERTS", path)
    return path


def test_alert_is_structured_and_fails(alert_file):
    with pytest.raises(alerts.AlertError, match="quality/data_quality"):
        alerts.alert_and_fail("quality", "data_quality", "2 checks failed", {"failed": ["a", "b"]}, run_id="r1")
    rec = json.loads(alert_file.read_text().splitlines()[0])
    assert rec["source"] == "quality" and rec["severity"] == "error" and rec["run_id"] == "r1"
    assert rec["details"] == {"failed": ["a", "b"]} and rec["ts"]


def test_injected_bugs(raw):
    sales, _, _, _ = clean(raw)
    assert set(inject(sales, "drop_country:France")["Country"]) == {"United Kingdom"}
    assert (inject(sales, "negative_price:1")["Price"].iloc[0] < 0)
    assert inject(sales, "").equals(sales)


def test_dag_runs_the_steps_in_order():
    """Parse the DAG file (Airflow itself is not installed in CI) and check it wires STEPS in sequence."""
    tree = ast.parse((ROOT / "orchestration" / "dags" / "retail_pipeline.py").read_text())
    steps = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                 if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "STEPS")
    assert steps == STEPS == ["extract", "transform", "load", "quality", "dbt_build", "tiering", "export"]
    src = (ROOT / "orchestration" / "dags" / "retail_pipeline.py").read_text()
    assert '"retries": 0' in src and "max_active_runs=1" in src and "upstream >> downstream" in src


def _run_steps(tmp_path, raw, schema, bug=""):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    raw.to_parquet(run_dir / "raw.parquet", index=False)   # stands in for the extract step
    codes = {}
    for s in ["transform", "load", "quality", "export"]:
        codes[s] = step([s, "--run-dir", str(run_dir), "--schema", schema, "--inject-bug", bug])
        if codes[s]:
            break
    return run_dir, codes


@pytest.fixture
def clean_schema(pg_dsn):
    yield "test_orch"
    with connect(schema="test_orch", dsn=pg_dsn) as conn:
        conn.execute("DROP SCHEMA IF EXISTS test_orch CASCADE")
        conn.commit()


def test_orchestrated_steps_succeed_on_clean_data(tmp_path, raw, clean_schema, alert_file):
    run_dir, codes = _run_steps(tmp_path, raw, clean_schema)
    assert codes == {"transform": 0, "load": 0, "quality": 0, "export": 0}
    summary = json.loads((run_dir / "run_summary.json").read_text())
    assert summary["table_rows"]["fact_sales"] == 2 and summary["fact_sales_revenue"] == 6.25
    assert not alert_file.exists()


def test_quality_failure_alerts_and_stops(tmp_path, raw, clean_schema, alert_file):
    _, codes = _run_steps(tmp_path, raw, clean_schema, bug="negative_price:1")
    assert codes == {"transform": 0, "load": 0, "quality": 1}           # export never ran
    rec = json.loads(alert_file.read_text().splitlines()[-1])
    assert rec["source"] == "quality"
    assert rec["details"]["failed"][0]["name"] == "positive_quantity_price_sales"


def test_parity_detects_a_dropped_row(pg_dsn, raw):
    sales, cancels, _, _ = clean(raw)
    try:
        for schema, s in [("test_par_a", sales), ("test_par_b", sales), ("test_par_c", sales.iloc[:-1])]:
            with connect(schema=schema, dsn=pg_dsn) as conn:
                load(conn, build_star(s, cancels))
        same = compare_warehouses("test_par_a", "test_par_b")
        assert all(c["match"] for c in same["checks"]) and not same["country_drilldown"]
        diff = compare_warehouses("test_par_a", "test_par_c")
        failed = {c["name"] for c in diff["checks"] if not c["match"]}
        assert {"fact_sales", "fact_sales.revenue", "fact_sales.country"} <= failed
        assert diff["country_drilldown"] == [{"country": "France", "legacy_rows": 1, "orchestrated_rows": 0}]
    finally:
        with connect(schema="test_par_a", dsn=pg_dsn) as conn:
            for schema in ["test_par_a", "test_par_b", "test_par_c"]:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()


def test_compare_tiers_counts_missing_and_changed():
    legacy = pd.DataFrame({"tier": ["Tier 1", "Tier 2", "Tier 3", "Tier 4"]}, index=[1, 2, 3, 4])
    assert compare_tiers(legacy, legacy.copy())["match"]
    orch = pd.DataFrame({"tier": ["Tier 1", "Tier 3", "Tier 3"]}, index=[1, 2, 3])
    r = compare_tiers(legacy, orch)
    assert (r["missing_in_orchestrated"], r["tier_changed"], r["match"]) == (1, 1, False)
    assert r["transitions"] == {"Tier 2 -> Tier 3": 1, "Tier 4 -> missing": 1}
    assert r["tier_agreement_pct"] == 50.0
