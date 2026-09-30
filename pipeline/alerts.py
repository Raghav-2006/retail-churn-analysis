"""Structured alerts: one JSON object per line in logs/alerts.jsonl.

Every orchestrated task and the parity check call alert_and_fail() on a failed quality
check, dbt test or parity comparison. It records the alert AND raises, so the task fails
and Airflow stops the downstream tasks: nothing continues silently on bad data.
"""
import json
import os
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALERTS = Path(os.environ.get("ALERTS_PATH", ROOT / "logs" / "alerts.jsonl"))


class AlertError(RuntimeError):
    pass


def write_alert(source: str, check: str, message: str, details: dict | None = None,
                severity: str = "error", run_id: str | None = None, path: Path | None = None) -> dict:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "severity": severity,
        "source": source,
        "check": check,
        "message": message,
        "run_id": run_id or os.environ.get("PIPELINE_RUN_ID"),
        "details": details or {},
    }
    path = path or ALERTS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record


def alert_and_fail(source: str, check: str, message: str, details: dict | None = None, **kw: object) -> None:
    write_alert(source, check, message, details, **kw)
    raise AlertError(f"[{source}/{check}] {message}")
