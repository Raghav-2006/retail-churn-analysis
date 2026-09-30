"""Async FastAPI service.

    uvicorn service.api:app --port 8000

GET  /health                    liveness + what the service is serving from
GET  /customers/{id}/tier       tier, score and the explain() breakdown for one customer
POST /ask                       answer a business question

/ask serves the cached answers from the frozen eval runs (demo mode). A question that is not
cached gets a clear 404 with the closest cached questions. Live mode (a real Gemini call against
the warehouse) is used only when ANALYST_LIVE=1 and GEMINI_API_KEY are both set; it is rate
limited and wrapped in a timeout, and on a timeout or error the API returns an explicit error.
It never falls back to guessing.

Every request is logged as one JSON line (stdout and logs/api.jsonl) with a request id, the
latency and whether the response came from the cache.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from service.cache import TTLCache
from service.ratelimit import RateLimiter
from service.store import DEFAULT_MODEL, DEFAULT_VERSION, DemoStore, normalize
from tiering.score import explain

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.0"
ASK_TIMEOUT_S = float(os.environ.get("ASK_TIMEOUT_S", "30"))
LIVE_LIMIT = RateLimiter(int(os.environ.get("LIVE_MAX_CALLS", "10")), per_seconds=3600)


# ---- structured logging -----------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, default=str)


def _logger() -> logging.Logger:
    log = logging.getLogger("service.api")
    if not log.handlers:
        log.setLevel(logging.INFO)
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if os.environ.get("API_LOG_FILE", "1") != "0":
            (ROOT / "logs").mkdir(exist_ok=True)
            handlers.append(logging.FileHandler(ROOT / "logs" / "api.jsonl"))
        for h in handlers:
            h.setFormatter(JsonFormatter())
            log.addHandler(h)
        log.propagate = False
    return log


log = _logger()


def live_enabled() -> bool:
    return os.environ.get("ANALYST_LIVE") == "1" and bool(os.environ.get("GEMINI_API_KEY"))


# ---- app --------------------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = DemoStore()
    app.state.cache = TTLCache(maxsize=1024, ttl_s=float(os.environ.get("CACHE_TTL_S", "600")))
    app.state.analyst = None  # created lazily, only in live mode
    log.info("startup", extra={"fields": {"demo_db": str(app.state.store.path), "live_mode": live_enabled()}})
    yield


app = FastAPI(title="Retail sales platform API", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as err:  # unhandled: log and return a clean 500, never a stack trace
        log.exception("unhandled error", extra={"fields": {"request_id": request_id, "path": request.url.path}})
        response = JSONResponse({"status": "error", "message": f"internal error: {type(err).__name__}"},
                                status_code=500)
    response.headers["x-request-id"] = request_id
    log.info("request", extra={"fields": {
        "request_id": request_id, "method": request.method, "path": request.url.path,
        "status": response.status_code, "latency_ms": round(1000 * (time.perf_counter() - t0), 2),
        "cache": response.headers.get("x-cache", "-")}})
    return response


def cached_json(request: Request, key: str, body: dict, status: int = 200) -> JSONResponse:
    request.app.state.cache.set(key, (status, body))
    return JSONResponse(body, status_code=status, headers={"x-cache": "MISS"})


@app.get("/health")
async def health(request: Request) -> dict:
    store: DemoStore = request.app.state.store
    return {
        "status": "ok", "version": VERSION, "backend": "demo-sqlite", "demo_db": store.path.name,
        "demo_db_built_at": store.meta["built_at"], "customers": len(store.scored),
        "cached_answers": len(store.answers), "live_mode": live_enabled(),
        "live_calls_remaining_this_hour": LIVE_LIMIT.remaining() if live_enabled() else 0,
        "response_cache": request.app.state.cache.stats(),
    }


@app.get("/customers/{customer_id}/tier")
async def customer_tier(customer_id: int, request: Request) -> JSONResponse:
    key = f"tier:{customer_id}"
    if (hit := request.app.state.cache.get(key)) is not None:
        return JSONResponse(hit[1], status_code=hit[0], headers={"x-cache": "HIT"})
    store: DemoStore = request.app.state.store
    if customer_id not in store.scored.index:
        return cached_json(request, key, {"status": "not_found", "message": (
            f"customer {customer_id} was not scored: tiers cover the {len(store.scored):,} customers with a "
            f"purchase before {store.meta['scoring_cutoff']}")}, status=404)
    ex = explain(customer_id, store.scored)
    row = store.scored.loc[customer_id]
    body = {**ex, "country": row["country"], "future_revenue_jun_nov_2011": round(float(row["future_revenue"]), 2),
            "scoring_cutoff": store.meta["scoring_cutoff"]}
    return cached_json(request, key, json.loads(json.dumps(body, default=float)))


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    model: str = DEFAULT_MODEL
    prompt_version: str = DEFAULT_VERSION


@app.post("/ask")
async def ask(body: AskRequest, request: Request) -> JSONResponse:
    key = f"ask:{body.model}:{body.prompt_version}:{normalize(body.question)}"
    if (hit := request.app.state.cache.get(key)) is not None:
        return JSONResponse(hit[1], status_code=hit[0], headers={"x-cache": "HIT"})
    store: DemoStore = request.app.state.store

    cached = store.cached_answer(body.question, body.model, body.prompt_version)
    if cached is not None:
        return cached_json(request, key, {
            "status": "answered_from_cache", "mode": "demo", "question": cached["question"],
            "model": body.model, "prompt_version": body.prompt_version,
            "abstained": cached["abstained"], "answer": cached["answer"], "sql": cached["sql"],
            "columns": cached["columns"], "rows": cached["rows"], "row_count": cached["row_count"],
            "error": cached["error"],
            "correctness": cached["outcome"],   # graded against the hand-written reference SQL
            "difficulty": cached["difficulty"], "eval_id": cached["qid"],
            "note": "Cached answer from the frozen eval run; answer text rendered from the SQL result."})

    if not live_enabled():
        return JSONResponse({"status": "not_cached", "mode": "demo", "question": body.question,
                             "message": ("This question is not in the cached evaluation answers, and live mode "
                                         "is off (it needs ANALYST_LIVE=1 and a GEMINI_API_KEY). No answer is "
                                         "given rather than a guess."),
                             "similar_cached_questions": store.similar_questions(body.question)},
                            status_code=404, headers={"x-cache": "MISS"})
    return await ask_live(body, request)


async def ask_live(body: AskRequest, request: Request) -> JSONResponse:
    """Live Gemini call with a hard rate limit, a timeout, and an explicit error fallback."""
    if not LIVE_LIMIT.allow():
        return JSONResponse({"status": "rate_limited", "message": "Live question limit reached for this hour."},
                            status_code=429)
    if request.app.state.analyst is None:
        from analyst.agent import Analyst

        request.app.state.analyst = Analyst(prompt_version=body.prompt_version, model=body.model)
    try:
        ans = await asyncio.wait_for(asyncio.to_thread(request.app.state.analyst.ask, body.question),
                                     timeout=ASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("live ask timeout", extra={"fields": {"timeout_s": ASK_TIMEOUT_S}})
        return JSONResponse({"status": "timeout", "fallback": True, "message": (
            f"The analyst did not answer within {ASK_TIMEOUT_S:.0f}s. No answer is given rather than a guess.")},
            status_code=504)
    except Exception as err:
        log.warning("live ask failed", extra={"fields": {"error": f"{type(err).__name__}: {err}"}})
        return JSONResponse({"status": "error", "fallback": True, "message": (
            f"The analyst failed ({type(err).__name__}). No answer is given rather than a guess.")},
            status_code=503)
    return JSONResponse({"status": "answered_live", "mode": "live", "question": body.question,
                         "abstained": ans.abstained, "answer": ans.answer, "sql": ans.sql, "columns": ans.columns,
                         "rows": ans.rows[:50], "error": ans.error, "correctness": "unknown (not in eval set)"},
                        headers={"x-cache": "MISS"})
