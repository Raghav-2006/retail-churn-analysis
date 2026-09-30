"""Judge vs human agreement on the 30 hand-labelled answers (Phase 8).

    python -m analyst.agreement        # reads labels/human_labels.csv, writes metrics/judge_agreement.json

Accuracy (share of answers where judge and human agree) and Cohen's kappa (agreement corrected for
chance), separately for faithfulness and citation correctness. Kappa reading used here:
< 0.4 poor (do not rely on the judge), 0.4-0.6 moderate, 0.6-0.8 substantial, > 0.8 almost perfect.
"""
import json
from pathlib import Path

from sklearn.metrics import cohen_kappa_score, confusion_matrix

from analyst.human_labels import CSV_PATH, KEY_PATH, read_labels
from src.metrics import load_metrics, save_metrics

LABELS = ("faithful", "citation_correct")


def verdict(kappa: float) -> str:
    if kappa != kappa:  # NaN: one rater used a single class
        return "undefined (a rater used only one label)"
    return ("poor: do not rely on the judge" if kappa < 0.4 else "moderate" if kappa < 0.6
            else "substantial" if kappa < 0.8 else "almost perfect")


def agreement(labels_path: Path = CSV_PATH, key_path: Path = KEY_PATH, verdicts: list[dict] | None = None) -> dict:
    """Join the hand labels to the judge's verdicts and score agreement. Raises if any label is missing."""
    human = read_labels(labels_path)
    key = json.loads(key_path.read_text())
    if verdicts is None:
        verdicts = load_metrics(f"judge_{next(iter(key.values()))['version']}")["verdicts"]
    judged = {(v["id"], v["run"]): v for v in verdicts}
    out = {"n": len(human)}
    rows = []
    for label_id, h in sorted(human.items()):
        k = key[label_id]
        j = judged[(k["id"], k["run"])]
        rows.append({"label_id": label_id, "id": k["id"], "run": k["run"],
                     **{f"human_{c}": h[c] for c in LABELS}, **{f"judge_{c}": int(j[c]) for c in LABELS},
                     "judge_faithfulness_reason": j["faithfulness_reason"], "judge_citation_reason": j["citation_reason"],
                     "notes": h["notes"]})
    for c in LABELS:
        hs, js = [r[f"human_{c}"] for r in rows], [r[f"judge_{c}"] for r in rows]
        kappa = float(cohen_kappa_score(hs, js)) if len(set(hs) | set(js)) > 1 else float("nan")
        out[c] = {"accuracy_pct": round(100 * sum(a == b for a, b in zip(hs, js, strict=True)) / len(rows), 1),
                  "cohens_kappa": round(kappa, 3) if kappa == kappa else None, "reading": verdict(kappa),
                  "human_positive": sum(hs), "judge_positive": sum(js),
                  "confusion_human_rows_judge_cols": confusion_matrix(hs, js, labels=[0, 1]).tolist(),
                  "disagreements": [r["label_id"] for r in rows if r[f"human_{c}"] != r[f"judge_{c}"]]}
    out["rows"] = rows
    return out


def main() -> dict:
    out = agreement()
    save_metrics("judge_agreement", out)
    for c in LABELS:
        print(f"{c}: accuracy {out[c]['accuracy_pct']}%, kappa {out[c]['cohens_kappa']} ({out[c]['reading']})")
    return out


if __name__ == "__main__":
    main()
