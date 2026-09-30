"""Export 30 v4 answers for hand labelling (judge calibration, Phase 8), and read the labels back.

    python -m analyst.human_labels export     # writes labels/human_labels.csv (+ labels/human_labels_key.json)

The sample is drawn from the answered v4 eval records (answers that state a result), BLIND: the
file contains no judge verdict and no eval grade. So that agreement is not measured only on easy
cases, the sample is stratified with signals that do not come from the judge:
  - every definition question (run 0) that was answered;
  - every answer whose SQL result was graded wrong;
  - answers where the deterministic number check (analyst/verify.py) finds a number that is not in
    the result;
  - the rest drawn at random (seed 7) from the other answered questions.
Rows are shuffled, one answer per question. Lines starting with "#" are instructions and are
ignored when the labels are read back.
"""
import csv
import json
import random
import sys
from pathlib import Path

from analyst.evaluate import metrics_name
from analyst.judge import MODEL, judgeable
from analyst.verify import unsupported_numbers
from rag.docs import load_docs
from src.metrics import METRICS

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "labels"
CSV_PATH = LABELS / "human_labels.csv"
KEY_PATH = LABELS / "human_labels_key.json"
N = 30
SEED = 7
FIELDS = ["label_id", "question", "sql", "sql_result", "answer", "cited_docs", "docs_shown_to_agent",
          "faithful", "citation_correct", "notes"]

INSTRUCTIONS = """HOW TO LABEL (30 rows). Fill in the three columns faithful, citation_correct and notes; leave everything else as it is.
faithful: 1 if EVERY number and factual claim in `answer` is supported by `sql_result` (the rows the SQL returned), else 0.
  Rounding and reformatting are fine: 445.6018 -> "£445.60", 0.387 -> "38.7%", 17068582.72 -> "£17.1 million", and so is counting the result rows.
  Put 0 for a number that is not in the result, a wrong rounding, a total/average/ranking the result does not contain, the wrong period/unit/entity, or an invented explanation.
  Judge ONLY answer vs result. Do NOT judge whether the SQL was the right query: a correct restatement of a wrong result is still faithful (1).
  A short phrase naming the definition used (e.g. "active = ordered in the last 90 days") is fine if the cited doc says that.
citation_correct: 1 if the docs in `cited_docs` are the right basis for the answer, else 0. Open the docs in knowledge/<doc id>.md.
  1 = every cited doc is current (not deprecated), relevant, and defines what the SQL actually computes; AND if the question depends on a business definition that one of `docs_shown_to_agent` provides, that doc is cited.
  1 is also right for an empty `cited_docs` when the question needs no business definition (plain column arithmetic such as "total revenue in 2010").
  0 = cites a deprecated doc, cites a doc whose definition the SQL does not follow, cites irrelevant docs as its basis, or omits a needed definition doc.
Use only 1 or 0 in the two label columns. If you are unsure, pick your best guess and write why in notes. Save as CSV (keep the file name). Then tell Claude the labels are done."""


def _records(version: str = "v4") -> list[dict]:
    recs = []
    for s in ("frozen", "defs"):
        for r in json.loads((METRICS / f"{metrics_name(version, MODEL, s)}_records.json").read_text()):
            recs.append(dict(r, set=s))
    return recs


def sample(recs: list[dict]) -> list[dict]:
    rng = random.Random(SEED)
    pool = [r for r in recs if judgeable(r)]
    picked: dict[str, dict] = {}

    def take(rs: list[dict], limit: int | None = None) -> None:
        for r in rs:
            if len(picked) >= N or (limit is not None and limit <= 0):
                return
            if r["id"] not in picked:
                picked[r["id"]] = r
                if limit is not None:
                    limit -= 1

    by_run0 = sorted(pool, key=lambda r: (r["run"], r["id"]))
    take([r for r in by_run0 if r["set"] == "defs" and r["run"] == 0])
    take([r for r in by_run0 if r["set"] == "defs"])
    take([r for r in by_run0 if r["outcome"] != "correct"])
    flagged = [r for r in by_run0 if unsupported_numbers(r["answer"], r["question"], r["columns"], r["result"])]
    take(flagged, limit=8)
    rest = [r for r in by_run0 if r["run"] == 0 and r["id"] not in picked]
    rng.shuffle(rest)
    take(rest)
    rows = list(picked.values())[:N]
    rng.shuffle(rows)
    return rows


def export(version: str = "v4") -> Path:
    docs = {d.id: d for d in load_docs()}
    rows = sample(_records(version))
    LABELS.mkdir(exist_ok=True)
    key = {}
    with CSV_PATH.open("w", newline="") as f:
        w = csv.writer(f)
        for line in INSTRUCTIONS.splitlines():
            w.writerow([f"# {line}"])
        w.writerow(FIELDS)
        for i, r in enumerate(rows, 1):
            label_id = f"L{i:02d}"
            key[label_id] = {"id": r["id"], "run": r["run"], "set": r["set"], "version": version}
            shown = list(dict.fromkeys(c.split("#")[0] for c in r["retrieved"]))
            cited = [f"{c}{' (DEPRECATED)' if c in docs and docs[c].deprecated else ''}" for c in r["citations"]]
            result = json.dumps({"columns": r["columns"], "rows": r["result"], "row_count": r["rows"]}, default=str)
            w.writerow([label_id, r["question"], r["sql"], result, r["answer"], "; ".join(cited) or "(none)",
                        "; ".join(shown), "", "", ""])
    KEY_PATH.write_text(json.dumps(key, indent=1) + "\n")
    print(f"wrote {CSV_PATH.relative_to(ROOT)} ({len(rows)} answers) and {KEY_PATH.relative_to(ROOT)}")
    return CSV_PATH


def read_labels(path: Path = CSV_PATH) -> dict[str, dict]:
    """label_id -> {"faithful": 0/1, "citation_correct": 0/1, "notes": str}; raises if any label is missing."""
    lines = [line for line in path.read_text().splitlines() if not line.lstrip('"').startswith("#")]
    out, missing = {}, []
    for row in csv.DictReader(lines):
        vals = {}
        for col in ("faithful", "citation_correct"):
            v = (row.get(col) or "").strip()
            if v not in {"0", "1"}:
                missing.append(f"{row['label_id']}.{col}={v!r}")
            vals[col] = int(v) if v in {"0", "1"} else None
        out[row["label_id"]] = {**vals, "notes": row.get("notes", "")}
    if missing:
        raise ValueError(f"labels missing or not 0/1: {', '.join(missing)}")
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["export"]:
        export()
    else:
        print(__doc__)
