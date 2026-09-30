"""Analyst guardrails and grading logic. No API key needed: the LLM is never called here."""
from datetime import datetime
from decimal import Decimal

import pytest

from analyst.agent import UnsafeSQL, validate_sql
from analyst.evaluate import grade, load_eval_set, results_match, summarise


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select sum(revenue) from fact_sales;",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT * FROM dim_product WHERE description ILIKE '%set%'",   # keyword inside a string is fine
    "-- total\nSELECT count(*) FROM fact_sales",
    "SELECT created FROM (SELECT 1 AS created) t",                   # keyword as part of a word
])
def test_allows_read_only_selects(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql,reason", [
    ("DROP TABLE fact_sales", "only SELECT"),
    ("DELETE FROM fact_sales", "only SELECT"),
    ("SELECT 1; DROP TABLE fact_sales", "single statement"),
    ("WITH d AS (DELETE FROM fact_sales RETURNING *) SELECT * FROM d", "DELETE"),
    ("SELECT pg_sleep(100)", "PG_SLEEP"),
    ("SELECT * INTO backup FROM fact_sales", "INTO"),
    ("SELECT set_config('statement_timeout', '0', false)", "SET_CONFIG"),
    ("", "empty"),
])
def test_rejects_anything_else(sql, reason):
    with pytest.raises(UnsafeSQL, match=reason):
        validate_sql(sql)


def test_readonly_role_cannot_write(pg_dsn):
    """Second layer: even if the guard missed something, the database refuses."""
    import psycopg

    from analyst.agent import run_readonly
    from pipeline.db import connect
    from pipeline.load import grant_readonly

    with connect(schema="test_ro", dsn=pg_dsn) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x int)")
        conn.commit()
        grant_readonly(conn, schema="test_ro")
    try:
        assert run_readonly("SELECT count(*) FROM t", schema="test_ro")[1] == [(0,)]
        with pytest.raises(psycopg.Error):
            run_readonly("INSERT INTO t VALUES (1)", schema="test_ro")
    finally:
        with connect(schema="test_ro", dsn=pg_dsn) as conn:
            conn.execute("DROP SCHEMA test_ro CASCADE")
            conn.commit()


def test_results_match_is_order_insensitive_and_tolerant():
    ref = [("UK", Decimal("100.004")), ("France", Decimal("20"))]
    assert results_match(ref, [["France", 20.0], ["UK", 100.0]])
    assert results_match(ref, [[1, "UK", 100.0, 5], [2, "France", 20.0, 6]])  # extra columns are fine
    assert not results_match(ref, [["UK", 100.0]])                           # missing row
    assert not results_match(ref, [["UK", 101.0], ["France", 20.0]])         # wrong number
    assert not results_match(ref, [["UK", 100.0], ["Spain", 20.0]])          # wrong label


def test_results_match_normalises_dates_and_numbers():
    ref = [(datetime(2011, 11, 1), Decimal("1.5"))]
    assert results_match(ref, [["2011-11-01", 1.5]])
    assert results_match(ref, [["2011-11-01T00:00:00", "1.5"]])
    assert not results_match(ref, [["2011-10-01", 1.5]])


class A:  # minimal stand-in for analyst.agent.Answer
    def __init__(self, abstained=False, error=None, rows=None):
        self.abstained, self.error, self.rows = abstained, error, rows or []


def test_grading_outcomes():
    q, u = {"difficulty": "easy"}, {"difficulty": "unanswerable"}
    ref = [(5,)]
    assert grade(q, A(rows=[[5]]), ref) == "correct"
    assert grade(q, A(rows=[[6]]), ref) == "wrong"
    assert grade(q, A(abstained=True), ref) == "abstained"
    assert grade(q, A(error="boom"), ref) == "error"
    assert grade(u, A(abstained=True), None) == "refused"
    assert grade(u, A(rows=[[1]]), None) == "answered_unanswerable"


def test_confidently_wrong_rate():
    records = [{"difficulty": "easy", "outcome": o, "attempts": 1} for o in ["correct", "wrong", "abstained", "error"]]
    records += [{"difficulty": "unanswerable", "outcome": o, "attempts": 1} for o in ["refused", "answered_unanswerable"]]
    s = summarise(records)
    assert s["confidently_wrong_pct"] == round(100 * 2 / 6, 2)
    assert s["execution_accuracy_pct"] == 25.0
    assert s["abstention_accuracy_pct"] == 50.0


EVAL_SET_V2_SHA256 = "da756242da03001b3bc19cc18bad107cf3c4266496b18d174eda719c9fec0049"


def test_eval_set_is_frozen():
    """v1, v2 and v3 were all compared on exactly this file; any edit must bump the eval-set version."""
    from analyst.evaluate import eval_set_sha

    assert eval_set_sha() == EVAL_SET_V2_SHA256


def test_eval_set_shape():
    items = load_eval_set()
    assert len(items) == 55
    assert len({i["id"] for i in items}) == len(items)
    unanswerable = [i for i in items if i["difficulty"] == "unanswerable"]
    assert len(unanswerable) == 12
    assert all("sql" in i for i in items if i["difficulty"] != "unanswerable")
    assert all("sql" not in i for i in unanswerable)


def test_month_keys_can_be_written_several_ways():
    """Found in the baseline audit: correct answers graded wrong because the month was 'YYYY-MM' or a number."""
    ref = [(datetime(2011, 1, 1), 100.0), (datetime(2011, 2, 1), 80.0)]
    assert results_match(ref, [["2011-01", 100.0], ["2011-02", 80.0]])
    assert results_match(ref, [[1, "January", 100.0], [2, "February", 80.0]])
    assert results_match(ref, [[2011, 1, 100.0], [2011, 2, 80.0]])
    assert results_match(ref, [["Jan 2011", 100.0], ["Feb 2011", 80.0]])
    assert not results_match(ref, [[1, 80.0], [2, 100.0]])        # months swapped: still wrong
    assert not results_match(ref, [["2011-03", 100.0], ["2011-02", 80.0]])


def test_month_number_alone_is_ambiguous_across_years():
    ref = [(datetime(2010, 11, 1), 5.0), (datetime(2011, 11, 1), 6.0)]
    assert not results_match(ref, [[11, 5.0], [11, 6.0]])
    assert results_match(ref, [["2010-11", 5.0], ["2011-11", 6.0]])


def test_percent_questions_accept_fractions_only_when_flagged():
    ref = [(Decimal("79.65"),)]
    assert results_match(ref, [[0.7965]], percent=True)
    assert results_match(ref, [[79.65]], percent=True)
    assert not results_match(ref, [[0.7965]])
    assert not results_match(ref, [[0.65]], percent=True)


def test_backoff_is_exponential_capped_and_respects_retry_delay():
    from analyst.agent import backoff_delay

    delays = [backoff_delay(i) for i in range(10)]
    assert delays[0] < 3 and 12 <= delays[3] <= 20 and max(delays) <= 90
    assert backoff_delay(0, "Please retry in 11.72s.") >= 12.72
    assert backoff_delay(0, "'retryDelay': '30s'") >= 31
