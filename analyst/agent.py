"""AI analyst: natural-language question -> read-only SQL -> result -> answer.

    python -m analyst.agent "Which 5 countries bought the most in 2011?"

Guardrails, in layers:
  1. The model may abstain ("I can't answer that from this data").
  2. validate_sql(): a single SELECT/WITH statement, no DDL/DML/admin keywords.
  3. The query runs as `analyst_ro`, a Postgres role that only has SELECT on the
     warehouse, with default_transaction_read_only and a statement timeout.
  4. At most MAX_ROWS rows are fetched.
On a SQL error the model gets one retry with the error message.
Every call is logged to logs/analyst.jsonl; generations are cached on disk.
"""
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from analyst import prompts
from pipeline.db import SCHEMA, get_dsn
from pipeline.load import READONLY_ROLE

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "analyst.jsonl"
CACHE_DIR = ROOT / "data" / "analyst_cache"   # one JSONL per model, so models can be evaluated in parallel
# Parts 1-3 compared gemini-3.5-flash, 3.5-flash-lite and 3.1-flash-lite. Phases 7-8 ran under a hard
# prepaid budget on 3.1-flash-lite only, and analyst/budget.py prices only that model, so it is the
# default. Override with GEMINI_MODEL (the spend guard then refuses to call an unpriced model).
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 15_000
ABSTAIN_TEXT = "I can't answer that from this data."

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|execute|prepare|"
    r"vacuum|analyze|cluster|reindex|lock|listen|notify|set|reset|refresh|comment|security|"
    r"pg_sleep\w*|pg_read_\w+|pg_write\w*|pg_terminate_backend|pg_cancel_backend|lo_\w+|dblink\w*|set_config)\b",
    re.IGNORECASE,
)


RETRYABLE = {429, 500, 503, 504}   # rate limit / quota, transient server errors, overload
MAX_API_ATTEMPTS = 12              # about 10 minutes of waiting at most per call


def backoff_delay(attempt: int, message: str = "", base: float = 2.0, cap: float = 90.0) -> float:
    """Exponential backoff with jitter: 2s, 4s, 8s, ... capped at 90s.
    If the API says how long to wait ("retryDelay": "11s"), wait at least that long."""
    delay = min(cap, base * 2 ** attempt * random.uniform(0.75, 1.25))
    if m := re.search(r"retry(?:Delay)?\W+(?:in\s+)?(\d+(?:\.\d+)?)s", message, re.IGNORECASE):
        delay = max(delay, float(m.group(1)) + 1.0)
    return round(delay, 2)


class UnsafeSQL(ValueError):
    pass


class QuotaExhausted(RuntimeError):
    """The API key's daily request quota for this model is used up; retrying today will not help."""


def validate_sql(sql: str) -> str:
    """Return the cleaned statement, or raise UnsafeSQL. Defence in depth: the DB role is read-only too."""
    if not sql or not sql.strip():
        raise UnsafeSQL("empty SQL")
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)       # block comments
    s = re.sub(r"--[^\n]*", " ", s).strip()                # line comments
    s = s.rstrip().rstrip(";").strip()
    # Check keywords with string literals and quoted identifiers blanked out, so
    # WHERE description ILIKE '%set%' is fine but a real SET statement is not
    bare = re.sub(r"'(?:[^']|'')*'", "''", s)
    bare = re.sub(r'"(?:[^"]|"")*"', '""', bare)
    if ";" in bare:
        raise UnsafeSQL("only a single statement is allowed")
    if not re.match(r"^\s*(select|with)\b", bare, re.IGNORECASE):
        raise UnsafeSQL("only SELECT queries are allowed")
    if m := FORBIDDEN.search(bare):
        raise UnsafeSQL(f"forbidden keyword: {m.group(0).upper()}")
    if re.search(r"\binto\b", bare, re.IGNORECASE):
        raise UnsafeSQL("SELECT ... INTO is not allowed")
    return s


def readonly_dsn() -> str:
    return make_conninfo(get_dsn(), user=READONLY_ROLE, password=os.environ.get("ANALYST_DB_PASSWORD", READONLY_ROLE))


def run_readonly(sql: str, max_rows: int = MAX_ROWS, schema: str = SCHEMA) -> tuple[list[str], list[tuple], bool]:
    """Execute as the read-only role. Returns (columns, rows, truncated)."""
    with psycopg.connect(readonly_dsn(), autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        conn.execute(f"SET LOCAL search_path TO {schema}")
        cur = conn.execute(sql)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        conn.rollback()
    return cols, rows[:max_rows], len(rows) > max_rows


def jsonable(v: object) -> object:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


@dataclass
class Answer:
    question: str
    abstained: bool = False
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    answer: str = ""
    error: str | None = None
    attempts: int = 0
    latency_s: float = 0.0          # wall clock, excluding time spent backing off from 429/503s
    backoff_s: float = 0.0
    model: str = MODEL
    prompt_version: str = ""
    cached: bool = False
    self_check: str | None = None   # v3+: ok / fix / abstain
    citations: list[str] = field(default_factory=list)   # v4+: doc ids the model says it used
    retrieved: list[str] = field(default_factory=list)   # v4+: chunk ids shown to the model, in rank order
    narrate_s: float = 0.0          # time spent writing the prose answer (included in latency_s)
    verification: str | None = None  # v5: pass / corrected / abstained (numbers in the answer vs the result)
    unsupported: list[str] = field(default_factory=list)   # v5: numbers the first draft could not back up

    @property
    def success(self) -> bool:
        return self.abstained or (self.error is None and self.sql is not None)


class Analyst:
    def __init__(self, prompt_version: str = "v1", model: str = MODEL, narrate: bool = True,
                 use_cache: bool = True, cache_tag: str = "", log_path: Path = LOG_PATH,
                 cache_path: Path | None = None, retriever: object | None = None):
        from google import genai

        self.client = genai.Client()  # reads GEMINI_API_KEY from the environment
        self.model = model
        self.prompt = prompts.get(prompt_version)
        self.prompt_version = prompt_version
        self.narrate = narrate
        self.use_cache = use_cache
        self.cache_tag = cache_tag  # lets the evaluator keep independent runs apart in the cache
        self.log_path = log_path
        self.cache_path = cache_path or CACHE_DIR / f"{model}.jsonl"
        self.cache = self._load_cache() if use_cache else {}
        self._backoff = 0.0
        self.calls = 0   # Gemini calls made by this instance (cache hits make none)
        self._retriever = retriever
        self._context = ""   # v4+: retrieved knowledge for the question being answered
        self._hits: list = []

    @property
    def retriever(self) -> object:
        if self._retriever is None:
            from rag.retrieve import Retriever

            self._retriever = Retriever()
        return self._retriever

    def _msg(self, message: str) -> str:
        return prompts.with_context(self._context, message) if self._context else message

    # ---- LLM plumbing -------------------------------------------------------------
    def _generate(self, contents: str, json_mode: bool = True, purpose: str = "agent",
                  system: str | None = None) -> str:
        from google.genai import errors, types

        from analyst import budget

        config = types.GenerateContentConfig(
            system_instruction=system or self.prompt.system,
            temperature=0.0,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        for attempt in range(MAX_API_ATTEMPTS):
            budget.check(self.model)   # raises BudgetExceeded before a call that could pass the cap
            try:
                resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
                budget.record(self.model, f"{self.prompt_version}:{purpose}", resp.usage_metadata)
                self.calls += 1
                return resp.text or ""
            except (errors.ServerError, errors.ClientError) as err:
                code = getattr(err, "code", None)
                if code not in RETRYABLE or attempt == MAX_API_ATTEMPTS - 1:
                    if code == 429 and "PerDay" in str(err):
                        raise QuotaExhausted(f"daily request quota used up for {self.model}") from err
                    raise
                wait = backoff_delay(attempt, str(err))
                time.sleep(wait)
                self._backoff += wait
        raise RuntimeError("unreachable")

    @staticmethod
    def _parse(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?|```$", "", text).strip()
        for candidate in (text, (re.search(r"\{.*\}", text, re.S) or re.match("", "")).group(0)):
            try:
                out = json.loads(candidate)
                if isinstance(out, dict):
                    return out
            except json.JSONDecodeError:
                continue
        # Unparseable output: treated as empty SQL, which fails validation and triggers the retry
        return {"abstain": False, "sql": None}

    # ---- cache --------------------------------------------------------------------
    def _key(self, question: str) -> str:
        norm = " ".join(question.lower().split())
        return hashlib.sha256(f"{self.model}|{self.prompt_version}|{self.cache_tag}|{norm}".encode()).hexdigest()[:16]

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        entries = (json.loads(line) for line in self.cache_path.read_text().splitlines() if line.strip())
        return {e["key"]: e for e in entries}

    def _save_cache(self, key: str, ans: Answer) -> None:
        entry = {"key": key, **asdict(ans)}
        self.cache[key] = entry
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a") as f:
            f.write(json.dumps(entry, default=jsonable) + "\n")

    def _log(self, ans: Answer) -> None:
        self.log_path.parent.mkdir(exist_ok=True)
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "question": ans.question, "sql": ans.sql,
               "abstained": ans.abstained, "success": ans.success, "error": ans.error, "attempts": ans.attempts,
               "latency_s": ans.latency_s, "backoff_s": ans.backoff_s, "rows": len(ans.rows), "model": ans.model,
               "prompt_version": ans.prompt_version, "cached": ans.cached}
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    # ---- main loop ----------------------------------------------------------------
    def ask(self, question: str) -> Answer:
        key = self._key(question)
        if self.use_cache and key in self.cache:
            hit = {k: v for k, v in self.cache[key].items() if k != "key"}
            ans = Answer(**{**hit, "cached": True})
            self._log(ans)
            return ans

        ans = Answer(question=question, model=self.model, prompt_version=self.prompt_version)
        self._backoff = 0.0
        t0 = time.perf_counter()
        self._context, self._hits = "", []
        if self.prompt.rag:
            from rag.retrieve import format_context

            self._hits = self.retriever.retrieve(question)
            self._context = format_context(self._hits)
            ans.retrieved = [h.chunk_id for h in self._hits]
        contents = self._msg(self.prompt.user(question))
        for attempt in (1, 2):  # one retry on a SQL error, with the error fed back
            ans.attempts = attempt
            out = self._parse(self._generate(contents))
            if self.prompt.rag:
                ans.citations = [c for c in out.get("citations") or [] if isinstance(c, str)]
            if out.get("abstain"):
                ans.abstained, ans.sql, ans.error = True, None, None
                ans.answer = out.get("reason") or ABSTAIN_TEXT
                break
            sql = out.get("sql") or ""
            try:
                sql = validate_sql(sql)
                ans.columns, rows, ans.truncated = run_readonly(sql)
                if self.prompt.self_check:
                    verdict = self._self_check(question, sql, ans.columns, rows)
                    if verdict.get("abstain"):
                        ans.abstained, ans.sql, ans.error = True, None, None
                        ans.answer = verdict.get("reason") or ABSTAIN_TEXT
                        ans.self_check = "abstain"
                        break
                    if verdict.get("verdict") == "fix" and verdict.get("sql"):
                        sql = validate_sql(verdict["sql"])
                        ans.columns, rows, ans.truncated = run_readonly(sql)
                        ans.self_check = "fix"
                    else:
                        ans.self_check = "ok"
                ans.rows = [[jsonable(v) for v in r] for r in rows]
                ans.sql, ans.error = sql, None
                break
            except (UnsafeSQL, psycopg.Error) as err:
                ans.sql, ans.error = sql, f"{type(err).__name__}: {err}".strip()
                contents = self._msg(self.prompt.retry(question, sql, ans.error))
        if ans.abstained:
            pass
        elif ans.error is None and self.narrate:
            t_n = time.perf_counter()
            ans.answer = self._narrate(question, ans)
            ans.narrate_s = round(time.perf_counter() - t_n, 2)
            if self.prompt.verify:
                ans.verification, ans.answer, ans.unsupported = self.verify_answer(
                    question, ans.sql, ans.columns, ans.rows[:20], ans.answer)
                if ans.verification == "abstained":
                    ans.abstained = True
        elif ans.error is not None:
            ans.answer = "The query failed, so I have no answer."
        ans.backoff_s = round(self._backoff, 2)
        ans.latency_s = round(time.perf_counter() - t0 - self._backoff, 2)
        self._log(ans)
        if self.use_cache:
            self._save_cache(key, ans)
        return ans

    def verify_answer(self, question: str, sql: str, columns: list[str], rows: list[list],
                      answer: str) -> tuple[str, str, list[str]]:
        """Self-verification (v5): every number in the answer must be backed by the result it was
        written from. If one is not, rewrite once from the result; if the rewrite still has an
        unsupported number, abstain. Returns (status, final answer, unsupported numbers of the draft)."""
        from analyst.verify import unsupported_numbers

        bad = unsupported_numbers(answer, question, columns, rows, sql)
        if not bad:
            return "pass", answer, []
        payload = json.dumps({"columns": columns, "rows": rows[:20]}, default=str)
        fixed = self._generate(prompts.correct(question, sql, payload, answer, bad), json_mode=False,
                               purpose="verify", system=prompts.NARRATE_SYSTEM).strip()
        if not unsupported_numbers(fixed, question, columns, rows, sql):
            return "corrected", fixed, bad
        return "abstained", f"{ABSTAIN_TEXT} The answer could not be verified against the query result.", bad

    def _self_check(self, question: str, sql: str, columns: list[str], rows: list[tuple]) -> dict:
        """Mitigation (v3): show the model its SQL and a result preview; it confirms, fixes or abstains."""
        preview = json.dumps({"columns": columns, "rows": [[jsonable(v) for v in r] for r in rows[:10]],
                              "row_count": len(rows)}, default=str)
        return self._parse(self._generate(self._msg(prompts.check(question, sql, preview)), purpose="self_check"))

    def _narrate(self, question: str, ans: Answer) -> str:
        payload = json.dumps({"columns": ans.columns, "rows": ans.rows[:20], "truncated": ans.truncated}, default=str)
        cited = "\n\n".join(h.text for h in self._hits if h.doc_id in ans.citations and not h.deprecated)
        return self._generate(prompts.narrate(question, ans.sql, payload, cited), json_mode=False,
                              purpose="narrate", system=prompts.NARRATE_SYSTEM).strip()


def main() -> None:
    question = " ".join(sys.argv[1:]) or "What were the top 5 countries by revenue?"
    ans = Analyst(prompt_version=os.environ.get("ANALYST_PROMPT", "v2")).ask(question)
    print(f"Q: {question}\n")
    if ans.sql:
        print(f"SQL:\n{ans.sql}\n")
    print(f"A: {ans.answer}")
    print(f"\n({ans.attempts} attempt(s), {ans.latency_s}s{', cached' if ans.cached else ''})")


if __name__ == "__main__":
    main()
