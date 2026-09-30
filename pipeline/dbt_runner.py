"""Run dbt against the same database as the pipeline.

    python -m pipeline.dbt_runner build            # dbt build: run models + all tests
    python -m pipeline.dbt_runner docs generate

The connection comes from pipeline.db.get_dsn() (embedded pgserver or DATABASE_URL) and
is passed to dbt/profiles.yml through DBT_* environment variables, so there is one source
of truth for where the warehouse lives. Source and target schemas can be overridden, which
is how the migration parity check builds a second, isolated copy.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from pipeline.db import SCHEMA, get_dsn

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"
ANALYTICS_SCHEMA = "analytics"


def dbt_env(target_schema: str = ANALYTICS_SCHEMA) -> dict:
    info = conninfo_to_dict(get_dsn())
    env = dict(os.environ)
    env.update({
        "DBT_HOST": str(info.get("host") or "localhost"),   # a directory = Unix socket (pgserver)
        "DBT_PORT": str(info.get("port") or 5432),
        "DBT_USER": str(info.get("user") or "postgres"),
        "DBT_PASSWORD": str(info.get("password") or ""),
        "DBT_DBNAME": str(info.get("dbname") or "postgres"),
        "DBT_SCHEMA": target_schema,
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    })
    return env


def run_dbt(args: list[str], source_schema: str = SCHEMA, target_schema: str = ANALYTICS_SCHEMA,
            extra_vars: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    vars_ = {"source_schema": source_schema, **(extra_vars or {})}
    cmd = [sys.executable, "-m", "dbt.cli.main", *args, "--project-dir", str(DBT_DIR),
           "--profiles-dir", str(DBT_DIR), "--vars", json.dumps(vars_)]
    proc = subprocess.run(cmd, env=dbt_env(target_schema), cwd=DBT_DIR, text=True, capture_output=True)
    print(proc.stdout[-6000:])
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
        if check:
            raise RuntimeError(f"dbt {' '.join(args)} failed (exit {proc.returncode})")
    return proc


def run_results() -> dict:
    """Counts from the last dbt invocation (dbt/target/run_results.json)."""
    res = json.loads((DBT_DIR / "target" / "run_results.json").read_text())
    counts: dict[str, dict[str, int]] = {}
    for r in res["results"]:
        kind = r["unique_id"].split(".")[0]
        counts.setdefault(kind, {}).setdefault(r["status"], 0)
        counts[kind][r["status"]] += 1
    return counts


if __name__ == "__main__":
    run_dbt(sys.argv[1:] or ["build"])
