"""Airflow DAG: the orchestrated replacement for the legacy `python -m pipeline.run` job.

extract -> transform -> load -> quality -> dbt_build -> tiering -> export

Each task shells out to `python -m pipeline.steps <task>` with the PROJECT's interpreter
(PROJECT_PYTHON), not Airflow's: Airflow pins its own SQLAlchemy/pandas stack, so it lives
in its own virtualenv and the pipeline code keeps exactly the dependencies v1 was built with.

Failure policy: no retries (a data-quality failure will not fix itself on retry), and a
failed task stops everything downstream. Each failing step has already written a
structured alert to logs/alerts.jsonl before exiting non-zero.

Params (override per run with `airflow dags test retail_pipeline -c '{...}'`):
  run_name, schema (warehouse), analytics_schema (dbt target), inject_bug (parity demo only).
"""
import os

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

PROJECT_DIR = os.environ.get("PROJECT_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
PROJECT_PYTHON = os.environ.get("PROJECT_PYTHON", "python3")
STEPS = ["extract", "transform", "load", "quality", "dbt_build", "tiering", "export"]

with DAG(
    dag_id="retail_pipeline",
    description="Online Retail II: extract, clean, load, check, dbt, tier, export",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule="0 6 * * *",          # daily at 06:00 UTC once deployed; runs are manual here
    catchup=False,
    max_active_runs=1,             # two concurrent runs would truncate each other's tables
    default_args={"retries": 0, "owner": "data-platform"},
    params={
        "run_name": Param("orchestrated", type="string"),
        "schema": Param("orch_retail", type="string"),
        "analytics_schema": Param("orch_analytics", type="string"),
        "inject_bug": Param("", type="string"),
    },
    tags=["retail", "migration"],
) as dag:
    tasks = []
    for step in STEPS:
        tasks.append(BashOperator(
            task_id=step,
            cwd=PROJECT_DIR,
            bash_command=(
                f"{PROJECT_PYTHON} -m pipeline.steps {step} "
                "--run-dir data/runs/{{ params.run_name }} "
                "--schema {{ params.schema }} --analytics-schema {{ params.analytics_schema }} "
                "--inject-bug '{{ params.inject_bug }}'"
            ),
            env={"PIPELINE_RUN_ID": "{{ run_id }}", "GEMINI_API_KEY": ""},   # no task may call the LLM
            append_env=True,
        ))
    for upstream, downstream in zip(tasks, tasks[1:]):
        upstream >> downstream
