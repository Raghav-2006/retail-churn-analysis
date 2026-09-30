"""Run the Airflow DAG once, locally, the way the parity check and a developer do.

    python -m orchestration.run_dag --run-name orchestrated
    python -m orchestration.run_dag --run-name buggy --inject-bug drop_country:Norway

Uses `airflow dags test` (standalone: no scheduler or webserver, SQLite metadata DB in
.airflow/). Airflow lives in its own virtualenv (.venv-airflow) with the official constraints
file; the tasks themselves run with this project's Python (see the DAG docstring).
Returns Airflow's exit code: non-zero when any task failed.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIRFLOW_BIN = Path(os.environ.get("AIRFLOW_BIN", ROOT / ".venv-airflow" / "bin" / "airflow"))
AIRFLOW_HOME = ROOT / ".airflow"


def airflow_env() -> dict:
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    env.update({
        "AIRFLOW_HOME": str(AIRFLOW_HOME),
        "AIRFLOW__CORE__DAGS_FOLDER": str(ROOT / "orchestration" / "dags"),
        "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
        "PROJECT_DIR": str(ROOT),
        "PROJECT_PYTHON": sys.executable,
    })
    return env


def task_states(env: dict | None = None) -> dict:
    """State of every task in the most recent run, read from Airflow's own metadata DB."""
    env = env or airflow_env()
    runs = subprocess.run([str(AIRFLOW_BIN), "dags", "list-runs", "retail_pipeline", "-o", "json"],
                          env=env, capture_output=True, text=True)
    latest = max(json.loads(runs.stdout), key=lambda r: r["run_after"])
    tasks = subprocess.run([str(AIRFLOW_BIN), "tasks", "states-for-dag-run", "retail_pipeline", latest["run_id"],
                            "-o", "json"], env=env, capture_output=True, text=True)
    return {"run_id": latest["run_id"], "dag_state": latest["state"],
            "tasks": {t["task_id"]: t["state"] for t in json.loads(tasks.stdout)}}


def run(run_name: str, schema: str, analytics_schema: str, inject_bug: str = "", log: Path | None = None) -> int:
    env = airflow_env()
    if not (AIRFLOW_HOME / "airflow.db").exists():
        subprocess.run([str(AIRFLOW_BIN), "db", "migrate"], env=env, check=True, capture_output=True)
    conf = {"run_name": run_name, "schema": schema, "analytics_schema": analytics_schema, "inject_bug": inject_bug}
    log = log or ROOT / "logs" / f"airflow_{run_name}.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("w") as f:
        proc = subprocess.run([str(AIRFLOW_BIN), "dags", "test", "retail_pipeline", "-c", json.dumps(conf)],
                              env=env, stdout=f, stderr=subprocess.STDOUT)
    states = task_states(env)
    (ROOT / "data" / "runs" / run_name).mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "runs" / run_name / "airflow_states.json").write_text(json.dumps(states, indent=2))
    print(f"airflow dags test retail_pipeline ({run_name}) -> exit {proc.returncode}, "
          f"dag {states['dag_state']}: {states['tasks']}; log: {log.relative_to(ROOT)}")
    return proc.returncode


def record(name: str, run_name: str, inject_bug: str, code: int) -> None:
    """Keep the outcome of a demonstration run (task states + alerts it raised) for RESULTS.md."""
    run_dir = ROOT / "data" / "runs" / run_name
    states = json.loads((run_dir / "airflow_states.json").read_text())
    alerts_path = ROOT / "logs" / "alerts.jsonl"
    alerts = [json.loads(line) for line in alerts_path.read_text().splitlines()] if alerts_path.exists() else []
    path = ROOT / "metrics" / "orchestration.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[name] = {"inject_bug": inject_bug, "exit_code": code, **states,
                  "alerts": [a for a in alerts if a.get("run_id") == states["run_id"]]}
    path.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="orchestrated")
    ap.add_argument("--schema", default="orch_retail")
    ap.add_argument("--analytics-schema", default="orch_analytics")
    ap.add_argument("--inject-bug", default="")
    ap.add_argument("--record", help="save this run's outcome under this name in metrics/orchestration.json")
    a = ap.parse_args()
    code = run(a.run_name, a.schema, a.analytics_schema, a.inject_bug)
    if a.record:
        record(a.record, a.run_name, a.inject_bug, code)
    sys.exit(code)
