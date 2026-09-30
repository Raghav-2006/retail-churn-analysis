"""Read-only access to the demo database (demo/demo.sqlite), shared by the API and the dashboard.

Only needs the standard library and pandas, so it runs on Streamlit Community Cloud.
"""
import difflib
import json
import os
import re
import sqlite3
from functools import cached_property
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = Path(os.environ.get("DEMO_DB", ROOT / "demo" / "demo.sqlite"))
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_VERSION = "v3"


def normalize(question: str) -> str:
    """Same normalisation as service.build_demo: lower-case, single spaces, no punctuation."""
    return re.sub(r"[^a-z0-9 ]", "", " ".join(question.lower().split()))


class DemoStore:
    def __init__(self, path: Path = DEMO_DB) -> None:
        if not Path(path).exists():
            raise FileNotFoundError(f"demo database not found at {path}; run python -m service.build_demo")
        self.path = Path(path)
        self._con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)

    def table(self, name: str) -> pd.DataFrame:
        return pd.read_sql_query(f"SELECT * FROM {name}", self._con)

    @cached_property
    def meta(self) -> dict:
        return self.table("meta").iloc[0].to_dict()

    @cached_property
    def kpis(self) -> dict[str, dict]:
        return {r.key: {"value": r.value, "label": r.label} for r in self.table("kpis").itertuples()}

    @cached_property
    def scored(self) -> pd.DataFrame:
        """The output of tiering.score.score_customers(), so tiering.score.explain() works unchanged."""
        return self.table("customer_scores").set_index("customer_id")

    @cached_property
    def answers(self) -> pd.DataFrame:
        return self.table("eval_answers")

    def questions(self) -> pd.DataFrame:
        return (self.answers[["qid", "difficulty", "question"]].drop_duplicates("qid")
                .sort_values("qid").reset_index(drop=True))

    def cached_answer(self, question: str, model: str = DEFAULT_MODEL, version: str = DEFAULT_VERSION) -> dict | None:
        a = self.answers
        hit = a[(a["question_norm"] == normalize(question)) & (a["model"] == model) & (a["prompt_version"] == version)]
        if hit.empty:
            return None
        r = {k: (None if isinstance(v, float) and v != v else v) for k, v in hit.iloc[0].to_dict().items()}  # NaN -> None
        for col in ("columns", "rows", "reference_columns", "reference_rows"):
            r[col] = json.loads(r[col]) if r.get(col) else None
        r["abstained"] = bool(r["abstained"])
        return r

    def similar_questions(self, question: str, n: int = 3) -> list[str]:
        qs = self.questions()["question"].tolist()
        return difflib.get_close_matches(question, qs, n=n, cutoff=0.4)
