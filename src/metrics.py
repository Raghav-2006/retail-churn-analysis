"""Tiny helper so each notebook records its key numbers in metrics/*.json.

RESULTS.md is rendered from these files (python src/make_results.py), which
keeps every reported number traceable to the notebook that produced it.
"""
import json
from pathlib import Path

METRICS = Path(__file__).resolve().parents[1] / "metrics"


def save_metrics(name: str, values: dict) -> None:
    METRICS.mkdir(exist_ok=True)
    path = METRICS / f"{name}.json"
    path.write_text(json.dumps(values, indent=2, default=float) + "\n")
    print(f"saved metrics/{path.name} ({len(values)} keys)")


def load_metrics(name: str) -> dict:
    return json.loads((METRICS / f"{name}.json").read_text())
