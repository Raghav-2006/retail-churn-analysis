"""Gemini spend guard: count every call, estimate its cost from the returned token usage, and stop
cleanly before the prepaid credit runs out.

    python -m analyst.budget            # print the ledger so far

The ledger (metrics/gemini_ledger.json) is committed, so the running total survives across
sessions. Every Gemini call in this repo goes through Analyst._generate(), which calls
`check()` before the request and `record()` after it.

Prices are Google's list prices for gemini-3.1-flash-lite (USD per 1M tokens): input $0.25,
output $1.50, and thinking tokens are billed as output. USD -> CAD uses a deliberately high
1.40 so the estimate errs on the side of stopping early.
"""
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "metrics" / "gemini_ledger.json"
PRICES_USD_PER_M = {"gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50}}
USD_TO_CAD = 1.40
CAP_CAD = float(os.environ.get("GEMINI_CAP_CAD", "8.0"))
# Worst case used before a call's real usage is known: a long RAG prompt plus a long answer.
WORST_CALL_TOKENS = {"input": 6_000, "output": 2_000}


class BudgetExceeded(RuntimeError):
    """The estimated spend would pass the cap. Completed results are kept; nothing else runs."""


def call_cost_cad(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICES_USD_PER_M.get(model)
    if p is None:
        raise ValueError(f"no price for {model}; only {list(PRICES_USD_PER_M)} may be used under the budget")
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1e6 * USD_TO_CAD


def load() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"cap_cad": CAP_CAD, "usd_to_cad": USD_TO_CAD, "prices_usd_per_m": PRICES_USD_PER_M,
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "est_cad": 0.0,
            "by_purpose": {}}


def _save(ledger: dict) -> None:
    ledger["updated_at"] = datetime.now().isoformat(timespec="seconds")
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=1) + "\n")


def spent_cad() -> float:
    return load()["est_cad"]


def check(model: str) -> None:
    """Raise BudgetExceeded if one more worst-case call could take the total past the cap."""
    worst = call_cost_cad(model, WORST_CALL_TOKENS["input"], WORST_CALL_TOKENS["output"])
    total = spent_cad()
    if total + worst > CAP_CAD:
        raise BudgetExceeded(f"estimated spend CA${total:.4f} + one more call would pass the CA${CAP_CAD:.2f} cap")


def record(model: str, purpose: str, usage: object) -> float:
    """Add one call's usage (a google.genai UsageMetadata, or None) to the ledger; return its cost."""
    inp = int(getattr(usage, "prompt_token_count", 0) or 0)
    out = int(getattr(usage, "candidates_token_count", 0) or 0)
    thought = int(getattr(usage, "thoughts_token_count", 0) or 0)
    if usage is None:   # no usage returned: charge the worst case rather than nothing
        inp, out = WORST_CALL_TOKENS["input"], WORST_CALL_TOKENS["output"]
    cost = call_cost_cad(model, inp, out + thought)
    ledger = load()
    ledger["calls"] += 1
    ledger["input_tokens"] += inp
    ledger["output_tokens"] += out
    ledger["thought_tokens"] += thought
    ledger["est_cad"] = round(ledger["est_cad"] + cost, 6)
    p = ledger["by_purpose"].setdefault(purpose, {"calls": 0, "est_cad": 0.0})
    p["calls"] += 1
    p["est_cad"] = round(p["est_cad"] + cost, 6)
    _save(ledger)
    return cost


def estimate(label: str, calls: int, model: str, input_tokens: int, output_tokens: int) -> float:
    """Print and return the estimated cost of a planned batch; refuse it if it would pass the cap."""
    per_call = call_cost_cad(model, input_tokens, output_tokens)
    est, total = calls * per_call, spent_cad()
    print(f"[budget] {label}: ~{calls} calls x ~{input_tokens} in / {output_tokens} out tokens "
          f"= ~CA${est:.3f}; spent so far CA${total:.4f}; projected CA${total + est:.3f} of CA${CAP_CAD:.2f} cap",
          flush=True)
    if total + est > CAP_CAD:
        raise BudgetExceeded(f"{label}: projected CA${total + est:.3f} passes the CA${CAP_CAD:.2f} cap; not started")
    return est


def avg_call_tokens() -> tuple[int, int] | None:
    """Mean (input, output+thinking) tokens per call seen so far, for better estimates."""
    ledger = load()
    if not ledger["calls"]:
        return None
    return (ledger["input_tokens"] // ledger["calls"],
            (ledger["output_tokens"] + ledger["thought_tokens"]) // ledger["calls"])


# Phases 7-8 plan: (batch, Gemini calls, input tokens per call, output tokens per call). Calls per
# question from the smoke tests: v3 ~2.2 (SQL + self-check, sometimes a retry), v4 narrated ~3.3.
PLAN = [
    ("v3 (no RAG) on the 10 definition questions x2 runs", 44, 1_800, 250),
    ("v4 (RAG) on the frozen 55 x2 runs, narrated", 363, 3_500, 300),
    ("v4 (RAG) on the 10 definition questions x2 runs, narrated", 66, 3_500, 300),
    ("LLM judge on every v4 answer", 130, 3_000, 250),
    ("self-verification corrections + judge on the corrected answers", 80, 3_500, 300),
]


def print_plan(model: str = "gemini-3.1-flash-lite") -> float:
    total_calls, total = 0, 0.0
    for label, calls, tin, tout in PLAN:
        c = calls * call_cost_cad(model, tin, tout)
        total_calls, total = total_calls + calls, total + c
        print(f"  {label:<70} {calls:>4} calls  ~CA${c:.3f}")
    spent = spent_cad()
    print(f"  {'TOTAL':<70} {total_calls:>4} calls  ~CA${total:.3f}  (+ CA${spent:.4f} already spent; cap CA${CAP_CAD:.2f})")
    return total


if __name__ == "__main__":
    import sys

    if "--plan" in sys.argv:
        print_plan()
    else:
        print(json.dumps(load(), indent=1))
