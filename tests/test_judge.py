"""Phase 8 pieces that need no API: the number check behind self-verification, the judge's input,
and reading the hand labels back."""
import pytest

from analyst.human_labels import FIELDS, INSTRUCTIONS, read_labels
from analyst.judge import judge_input, judgeable
from analyst.verify import numbers, unsupported_numbers


def test_numbers_are_parsed_with_units_and_precision():
    got = {raw: (v, d) for v, d, raw in numbers("AOV £445.60, up 5.2%, £17.1 million total, 1,168 orders")}
    assert got["£445.60"] == (445.6, 2)
    assert got["5.2%"] == (5.2, 1)
    assert got["£17.1 million"] == (17_100_000.0, -5)
    assert got["1,168"] == (1168.0, 0)
    assert numbers("on 2011-09-30 12:50:00") == []


@pytest.mark.parametrize("answer, rows", [
    ("The average order value was £445.60.", [[445.6018]]),
    ("The churn rate was 38.7%.", [[0.387022]]),          # fraction reported as a percent
    ("Revenue was £17.1 million.", [[17068582.72]]),
    ("Cancellations came to £1,095,136.79.", [[-1095136.79]]),  # sign flip
    ("The top 3 countries were the UK, EIRE and France.", [["UK", 1.0], ["EIRE", 2.0], ["France", 3.0]]),
    ("In Q3 2011 and on 3 March, 2,145 customers were active.", [[2145]]),
])
def test_supported_numbers_pass(answer, rows):
    assert unsupported_numbers(answer, "question", ["c"], rows) == []


def test_unsupported_numbers_are_flagged():
    bad = unsupported_numbers("Revenue was £18.1 million, 5% of the total, from 2,146 customers.",
                              "How much revenue?", ["r"], [[17068582.72]])
    assert bad == ["£18.1 million", "5%", "2,146"]


def test_judge_sees_result_answer_and_shown_docs_but_not_the_grade():
    rec = {"question": "What was AOV in 2011?", "sql": "SELECT 1", "columns": ["aov"], "result": [[445.6]],
           "rows": 1, "answer": "£445.60", "citations": ["metric-aov"], "outcome": "correct",
           "retrieved": ["metric-aov#0", "metric-aov-legacy#0"]}
    text = judge_input(rec)
    assert "445.6" in text and "metric-aov-legacy" in text and "DEPRECATED, superseded by metric-aov" in text
    assert "correct" not in text.lower().replace("citation correctness", "")
    assert judgeable(rec) and not judgeable({**rec, "outcome": "abstained"})


def test_labels_round_trip(tmp_path):
    import csv

    p = tmp_path / "labels.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        for line in INSTRUCTIONS.splitlines():
            w.writerow([f"# {line}"])
        w.writerow(FIELDS)
        w.writerow(["L01", "q", "SELECT 1,\n2", "{}", "a", "(none)", "x", "1", "0", "multi, line"])
        w.writerow(["L02", "q", "s", "{}", "a", "(none)", "x", " 0 ", "1", ""])
    assert read_labels(p) == {"L01": {"faithful": 1, "citation_correct": 0, "notes": "multi, line"},
                              "L02": {"faithful": 0, "citation_correct": 1, "notes": ""}}
    with p.open("a", newline="") as f:
        csv.writer(f).writerow(["L03", "q", "s", "{}", "a", "(none)", "x", "yes", "", ""])
    with pytest.raises(ValueError, match="L03"):
        read_labels(p)


def test_numbers_written_in_the_sql_count_as_supported():
    from analyst.verify import sql_literals

    sql = "SELECT count(*) FROM t WHERE d >= DATE '2011-10-01' - INTERVAL '90 days' HAVING sum(v) >= 1000"
    assert sql_literals(sql) == {90.0, 1000.0}   # the quoted date contributes nothing
    answer = "2,145 customers ordered in the 90 days to 30 September; large means at least £1,000."
    assert unsupported_numbers(answer, "q", ["n"], [[2145]], sql) == []
    assert unsupported_numbers(answer, "q", ["n"], [[2145]]) == ["90", "£1,000"]


def _agent_with(replies):
    from analyst import prompts
    from analyst.agent import Analyst

    a = Analyst.__new__(Analyst)
    a.prompt = prompts.get("v5")
    calls = []
    a._generate = lambda contents, **kw: (calls.append(kw["purpose"]), replies.pop(0))[1]
    return a, calls


def test_self_verification_passes_rewrites_or_abstains():
    q, sql, cols, rows = "How many orders?", "SELECT count(*) FROM fact_sales", ["n"], [[36594]]
    a, calls = _agent_with([])
    assert a.verify_answer(q, sql, cols, rows, "There were 36,594 orders.") == ("pass", "There were 36,594 orders.", [])
    assert calls == []                                               # a clean answer costs no call
    a, calls = _agent_with(["There were 36,594 orders."])
    status, final, bad = a.verify_answer(q, sql, cols, rows, "There were 46,594 orders.")
    assert (status, final, bad, calls) == ("corrected", "There were 36,594 orders.", ["46,594"], ["verify"])
    a, _ = _agent_with(["Roughly 40,000 orders."])
    status, final, _ = a.verify_answer(q, sql, cols, rows, "There were 46,594 orders.")
    assert status == "abstained" and final.startswith("I can't answer")


def test_agreement_script_runs_on_a_filled_label_file(tmp_path):
    """Synthetic labels in a temp dir only (the real labels/human_labels.csv is filled in by hand)."""
    import csv
    import json

    from analyst.agreement import agreement

    labels, key = tmp_path / "labels.csv", tmp_path / "key.json"
    human = [(1, 1), (1, 0), (0, 0), (1, 1)]
    judge = [(1, 1), (1, 1), (0, 0), (0, 1)]
    with labels.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["# instructions"])
        w.writerow(FIELDS)
        for i, (fa, ci) in enumerate(human):
            w.writerow([f"L{i}", "q", "s", "{}", "a", "(none)", "x", fa, ci, ""])
    key.write_text(json.dumps({f"L{i}": {"id": f"q{i}", "run": 0, "set": "frozen", "version": "v4"} for i in range(4)}))
    verdicts = [{"id": f"q{i}", "run": 0, "faithful": bool(fa), "citation_correct": bool(ci),
                 "faithfulness_reason": "", "citation_reason": ""} for i, (fa, ci) in enumerate(judge)]
    out = agreement(labels, key, verdicts)
    assert out["n"] == 4
    assert out["faithful"]["accuracy_pct"] == 75.0 and out["faithful"]["disagreements"] == ["L3"]
    assert out["citation_correct"]["accuracy_pct"] == 75.0 and out["citation_correct"]["cohens_kappa"] == 0.5
