import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.perf_insights import (build_performance, cache_summary, gen_speed, prompt_speed, speed_block, summarize)


def row(**kw):
    base = {"ts": 1000.0, "gateway_id": 1, "model_id": 10, "ok": True, "stream": True, "cached": False, "cache_state": None, "attempts": 1,
            "prompt_tokens": 400, "completion_tokens": 100, "ttft_ms": 500, "total_ms": 5500, "queue_ms": 0}
    return {**base, **kw}


# ---------------------------------------------------------------- per-request speeds
def test_generation_and_prompt_speed_basic():
    r = row()  # 100 tokens generated in (5500 - 500) ms = 5 s -> 20 tok/s ; 400 prompt tokens in 0.5 s -> 800 tok/s
    assert gen_speed(r) == pytest.approx(20.0) and prompt_speed(r) == pytest.approx(800.0)


def test_queue_time_is_excluded_from_generation_time():
    r = row(queue_ms=2000, total_ms=7500)  # same 5 s of real generation, but 2 s were spent waiting for a slot
    assert gen_speed(r) == pytest.approx(20.0)


@pytest.mark.parametrize("kw", [dict(stream=False), dict(cached=True), dict(ok=False), dict(attempts=2), dict(completion_tokens=3),
                                dict(ttft_ms=None), dict(total_ms=520), dict(ttft_ms=5400, total_ms=5500)])
def test_unmeasurable_requests_give_no_generation_speed(kw):
    assert gen_speed(row(**kw)) is None


@pytest.mark.parametrize("kw", [dict(stream=False), dict(cached=True), dict(ok=False), dict(attempts=3), dict(prompt_tokens=10),
                                dict(ttft_ms=None), dict(ttft_ms=5)])
def test_unmeasurable_requests_give_no_prompt_speed(kw):
    assert prompt_speed(row(**kw)) is None


def test_summarize():
    assert summarize([]) is None
    s = summarize([10, 20, 30, 40, 100])
    assert s["n"] == 5 and s["avg"] == 40 and s["p50"] == 30 and s["min"] == 10 and s["max"] == 100 and s["p95"] == 100
    assert summarize([7])["p50"] == 7 and summarize([7])["p95"] == 7


def test_speed_block_ignores_unmeasurable_rows():
    sb = speed_block([row(), row(completion_tokens=200, total_ms=10500), row(stream=False), row(cached=True)])
    assert sb["gen_tps"]["n"] == 2 and sb["gen_tps"]["avg"] == pytest.approx(20.0) and sb["prompt_tps"]["n"] == 2
    assert sb["ttft_ms"]["n"] == 2  # TTFT only from live streams
    assert speed_block([row(stream=False)]) == {"gen_tps": None, "prompt_tps": None, "ttft_ms": None}


# ---------------------------------------------------------------- cache numbers
def test_cache_summary():
    rows = [row(cache_state="hit", cached=True, prompt_tokens=100, completion_tokens=50), row(cache_state="hit", cached=True, stream=False),
            row(cache_state="miss"), row(cache_state="miss"), row(cache_state="miss"), row(cache_state="skip"), row(cache_state="bypass"),
            row(cache_state=None), row(cached=True, cache_state=None, prompt_tokens=10, completion_tokens=5)]  # legacy hit row without cache_state
    c = cache_summary(rows)
    assert (c["hits"], c["tracked_hits"], c["misses"], c["skipped"], c["bypassed"]) == (3, 2, 3, 1, 1)
    assert c["eligible"] == 5 and c["hit_rate"] == pytest.approx(2 / 5)   # the legacy hit row (no recorded outcome) is excluded from the rate
    assert c["stream_hits"] == 2
    assert c["tokens_saved"] == 150 + 500 + 15   # hit rows only: (100+50) + default (400+100) + legacy (10+5)
    assert cache_summary([row()])["hit_rate"] is None and cache_summary([])["eligible"] == 0


# ---------------------------------------------------------------- whole structure
def test_build_performance_groups_by_gateway_and_model():
    rows = [row(gateway_id=1, model_id=10, ts=1000 + i) for i in range(10)] + \
           [row(gateway_id=2, model_id=20, ts=1100 + i, completion_tokens=50, total_ms=5500, prompt_tokens=800, ttft_ms=1000) for i in range(4)] + \
           [row(gateway_id=2, model_id=20, ts=1150, cache_state="hit", cached=True, prompt_tokens=800, completion_tokens=50),
            row(gateway_id=2, model_id=20, ts=1151, stream=False, cache_state="miss")]
    out = build_performance(rows, {1: "alpha", 2: "beta"}, {10: {"upstream": "m-10", "endpoint": "e1"}, 20: {"upstream": "m-20", "endpoint": "e2"}},
                            since=1000.0, until=1200.0, buckets=4)
    gws = {g["name"]: g for g in out["gateways"]}
    assert out["gateways"][0]["name"] == "alpha" and gws["alpha"]["requests"] == 10 and gws["beta"]["requests"] == 6   # sorted by traffic
    assert gws["alpha"]["gen_tps"]["p50"] == pytest.approx(20.0) and gws["beta"]["gen_tps"]["p50"] == pytest.approx(50 / 4.5)
    assert gws["beta"]["prompt_tps"]["p50"] == pytest.approx(800.0) and gws["beta"]["cache"]["hits"] == 1 and gws["beta"]["cache"]["misses"] == 1
    assert gws["beta"]["streamed"] == 5 and gws["alpha"]["errors"] == 0
    models = {m["upstream"]: m for m in out["models"]}
    assert models["m-10"]["gen_tps"]["n"] == 10 and models["m-20"]["requests"] == 5   # cache hits are not counted for the upstream model
    assert out["models"][0]["upstream"] == "m-10"                                      # fastest first
    ser = {s["name"]: s["points"] for s in out["timeline"]["series"]}
    assert len(out["timeline"]["starts"]) == 4 and len(ser["alpha"]) == 4 and ser["alpha"][0] == pytest.approx(20.0) and ser["alpha"][3] is None
    assert out["totals"]["gen_tps"]["n"] == 14 and out["totals"]["cache"]["hits"] == 1
    assert build_performance([], {}, {}, since=0, until=60)["gateways"] == []


# ---------------------------------------------------------------- API
def test_performance_endpoint():
    from sqlmodel import Session
    from app.models import RequestLog
    db.init_db()
    now = time.time()
    with Session(db.engine) as s:
        for i in range(6):
            s.add(RequestLog(ts=now - 30 + i, gateway_id=424242, public_model="m", stream=True, ok=True, status=200, attempts=1, prompt_tokens=500,
                             completion_tokens=100, ttft_ms=250, total_ms=5250, queue_ms=0, cache_state="miss" if i < 4 else "hit", cached=i >= 4))
        s.add(RequestLog(ts=now - 5, gateway_id=424242, public_model="m", stream=False, ok=True, status=200, attempts=1, prompt_tokens=10, completion_tokens=10,
                         ttft_ms=900, total_ms=900, cache_state="skip"))
        s.commit()
    c = TestClient(app)
    r = c.get("/api/insights/performance?minutes=5").json()
    g = next(x for x in r["gateways"] if x["gateway_id"] == 424242)
    assert g["requests"] == 7 and g["gen_tps"]["n"] == 4 and g["gen_tps"]["p50"] == pytest.approx(20.0) and g["prompt_tps"]["p50"] == pytest.approx(2000.0)
    assert g["cache"]["hits"] == 2 and g["cache"]["misses"] == 4 and g["cache"]["skipped"] == 1 and g["cache"]["hit_rate"] == pytest.approx(2 / 6)
    assert g["cache"]["tracked_hits"] == 2
    assert r["cache_live"]["backend"] in ("memory", "redis") and r["meta"]["buckets"] == 24 and r["meta"]["notes"]["measured_on"]
    assert c.get("/api/insights/performance?minutes=0").status_code == 200  # clamped, not an error
