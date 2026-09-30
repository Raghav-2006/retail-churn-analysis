"""Streamlit dashboard, run headless with AppTest on the committed demo database."""
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dashboard import charts
from service.store import DemoStore

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_overview_kpis_come_from_the_demo_db(app):
    labels = {m.label: m.value for m in app.metric}
    assert labels["Customers"] == "5,852"
    assert labels["Migration parity"] == "100% clean run"
    assert labels["AI analyst: confidently wrong (v1 → v3)"] == "3.4% → 0.2%"
    assert labels["Scores match v1 after dbt migration"] == "Yes, bit for bit"
    assert labels["Definition questions answered correctly (no RAG → RAG)"] == "20% → 100%"
    assert labels["Confidently wrong, 65 questions (no RAG → RAG + self-check)"] == "9.2% → 1.5%"
    assert labels["LLM judge vs human agreement"] == "Pending human labels"


def test_tier_explorer_shows_the_explain_breakdown(app):
    labels = {m.label: m.value for m in app.metric}
    assert labels["Tier"] == "Tier 3" and labels["Score"] == "60.1 / 100"   # customer 12346 by default


def test_ask_demo_mode_only_without_secrets(app):
    assert app.radio[0].options == ["Demo: cached eval answers"]


def test_ask_labels_a_confidently_wrong_answer(app):
    q = "What was net revenue, after cancellations, in each calendar year?"   # x01: fan-out join, wrong 3/3
    {b.label: b for b in app.selectbox}["Question"].set_value(q)
    app.run()   # the model and prompt choices depend on the question (definition questions have v3-v5 only)
    boxes = {b.label: b for b in app.selectbox}
    boxes["Model"].set_value("gemini-3.1-flash-lite")
    boxes["Prompt"].set_value("v1")
    app.run()
    text = " ".join(m.value for m in app.markdown)
    assert DemoStore().cached_answer(q, "gemini-3.1-flash-lite", "v1")["outcome"] == "wrong"
    assert charts.OUTCOME_LABEL["wrong"] in text           # "Confidently wrong: ..."
    assert any("Reference answer" in e.label for e in app.expander)   # the right answer is shown


def test_ask_defaults_to_rag_and_shows_citations(app):
    boxes = {b.label: b for b in app.selectbox}
    assert boxes["Model"].value == "gemini-3.1-flash-lite" and boxes["Prompt"].value == "v5"
    boxes["Question"].set_value("How many large orders did we receive in 2011?")
    app.run()
    text = " ".join(m.value for m in app.markdown)
    assert "Cited definitions" in text and "metric-large-order" in text
    assert any("Self-verification passed" in c.value for c in app.caption)
    assert any("Uncalibrated" in c.value for c in app.caption)


def test_live_mode_appears_only_with_both_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-never-called")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere/none")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert at.radio[0].options == ["Demo: cached eval answers", "Live: ask Gemini"]


def test_charts_build():
    s = DemoStore()
    scores = s.scored.reset_index()
    for chart in [charts.monthly_revenue(s.table("monthly")), charts.tier_future_revenue(scores),
                  charts.capture_curve(scores), charts.capture_curve(scores, dark=True)]:
        assert chart.to_dict()
    from tiering.score import explain
    assert charts.contributions(pd.DataFrame(explain(12346, s.scored)["breakdown"])).to_dict()
