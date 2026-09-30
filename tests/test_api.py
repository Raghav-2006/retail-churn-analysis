"""FastAPI service on the committed demo database. No test calls Gemini: live mode uses a fake analyst."""
import time

import pytest
from fastapi.testclient import TestClient

from service import api
from service.cache import TTLCache
from service.ratelimit import RateLimiter
from tiering.score import explain

Q = "What was the total revenue across all sales?"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_LOG_FILE", "0")
    monkeypatch.delenv("ANALYST_LIVE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(api.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["customers"] == 4908 and j["cached_answers"] == 495
    assert j["live_mode"] is False and r.headers["x-request-id"]


def test_customer_tier_matches_explain_and_is_cached(client):
    r = client.get("/customers/12346/tier")
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    j = r.json()
    ex = explain(12346, client.app.state.store.scored)
    assert (j["tier"], j["score"], j["rank"]) == (ex["tier"], ex["score"], ex["rank"]) == ("Tier 3", 60.1, 1679)
    assert sum(b["points"] for b in j["breakdown"]) == pytest.approx(j["score"], abs=0.2)
    assert j["future_revenue_jun_nov_2011"] == 0.0
    assert client.get("/customers/12346/tier").headers["x-cache"] == "HIT"


def test_unknown_customer_is_a_clear_404(client):
    r = client.get("/customers/1/tier")
    assert r.status_code == 404 and r.json()["status"] == "not_found" and "4,908" in r.json()["message"]


def test_ask_returns_cached_eval_answer_with_correctness(client):
    r = client.post("/ask", json={"question": Q})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "answered_from_cache" and j["mode"] == "demo"
    assert j["correctness"] == "correct" and j["rows"] == [[17068582.72]] and "SUM(revenue)" in j["sql"]
    # same question, different punctuation and case: served from the response cache
    assert client.post("/ask", json={"question": "what was the TOTAL revenue across all sales"}).headers["x-cache"] == "HIT"


def test_ask_shows_a_confidently_wrong_answer_as_such(client):
    j = client.post("/ask", json={"question": "What was our profit margin in 2011?",
                                  "model": "gemini-3.1-flash-lite", "prompt_version": "v1"}).json()
    assert j["correctness"] == "answered_unanswerable" and j["abstained"] is False
    j = client.post("/ask", json={"question": "What was our profit margin in 2011?"}).json()   # v3
    assert j["correctness"] == "refused" and j["abstained"] is True


def test_uncached_question_is_refused_not_guessed(client):
    r = client.post("/ask", json={"question": "What is our average basket size by weekday?"})
    j = r.json()
    assert r.status_code == 404 and j["status"] == "not_cached" and "rather than a guess" in j["message"]
    assert j["similar_cached_questions"]


def test_request_validation(client):
    assert client.post("/ask", json={"question": "x"}).status_code == 422


class FakeAnalyst:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def ask(self, question):
        if self.behaviour == "slow":
            time.sleep(2)
        raise RuntimeError("model unavailable")


@pytest.fixture
def live_client(monkeypatch):
    monkeypatch.setenv("API_LOG_FILE", "0")
    monkeypatch.setenv("ANALYST_LIVE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-never-used")
    monkeypatch.setattr(api, "ASK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(api, "LIVE_LIMIT", RateLimiter(2, 3600))
    with TestClient(api.app) as c:
        yield c


def test_live_timeout_returns_explicit_error(live_client):
    live_client.app.state.analyst = FakeAnalyst("slow")
    r = live_client.post("/ask", json={"question": "A question that is not cached at all?"})
    assert r.status_code == 504 and r.json()["status"] == "timeout" and r.json()["fallback"] is True


def test_live_error_returns_explicit_error_then_rate_limit(live_client):
    live_client.app.state.analyst = FakeAnalyst("error")
    r = live_client.post("/ask", json={"question": "Another uncached question?"})
    assert r.status_code == 503 and r.json()["status"] == "error" and "rather than a guess" in r.json()["message"]
    live_client.post("/ask", json={"question": "A third uncached question?"})
    assert live_client.post("/ask", json={"question": "A fourth uncached question?"}).status_code == 429


def test_ttl_cache_and_rate_limiter():
    c = TTLCache(maxsize=2, ttl_s=60)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None and c.get("c") == 3        # LRU eviction
    rl = RateLimiter(2, 60)
    assert rl.allow() and rl.allow() and not rl.allow() and rl.remaining() == 0
