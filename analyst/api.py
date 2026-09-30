"""Optional HTTP front end:  uvicorn analyst.api:app --port 8000

    curl -s localhost:8000/ask -H 'content-type: application/json' \
         -d '{"question": "Top 5 countries by revenue?"}'
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel

from analyst.agent import Analyst

app = FastAPI(title="Retail analyst")
_agent: Analyst | None = None


class Ask(BaseModel):
    question: str


def agent() -> Analyst:
    global _agent
    if _agent is None:
        _agent = Analyst(prompt_version=os.environ.get("ANALYST_PROMPT", "v2"))
    return _agent


@app.post("/ask")
def ask(body: Ask) -> dict:
    a = agent().ask(body.question)
    return {"answer": a.answer, "abstained": a.abstained, "sql": a.sql, "columns": a.columns,
            "rows": a.rows[:50], "truncated": a.truncated, "error": a.error, "latency_s": a.latency_s}
