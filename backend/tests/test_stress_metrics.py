import pytest

from app.stress import make_prompt
from app.stress_metrics import (analyze, integrate_tokens, should_stop, stage_metrics, validate_config)


def rec(start=0.0, first=0.5, end=5.5, tokens=100, ok=True, queue_ms=0, prompt_tokens=400, status=200, upstream="m1", **kw):
    return {"start": start, "first": first, "end": end, "tokens": tokens, "ok": ok, "queue_ms": queue_ms, "prompt_tokens": prompt_tokens,
            "status": status, "upstream": upstream, **kw}


# ---------------------------------------------------------------- window integration
def test_integrate_tokens_spreads_each_request_over_its_generation_time():
    r = rec(first=2.0, end=6.0, tokens=100)                       # 25 tok/s from t=2..6
    assert integrate_tokens([r], 0, 10) == pytest.approx(100)    # fully inside
    assert integrate_tokens([r], 4, 10) == pytest.approx(50)     # half of it inside
    assert integrate_tokens([r], 6, 10) == pytest.approx(0)      # finished before the window
    assert integrate_tokens([r], 3, 5) == pytest.approx(50)      # window inside the request


def test_integrate_tokens_ignores_failures_and_handles_instant_requests():
    assert integrate_tokens([rec(ok=False)], 0, 10) == 0
    assert integrate_tokens([rec(first=None)], 0, 10) == 0
    assert integrate_tokens([rec(first=3.0, end=3.0, tokens=7)], 0, 10) == 7 and integrate_tokens([rec(first=3.0, end=3.0, tokens=7)], 5, 10) == 0


def test_stage_metrics_steady_state_throughput():
    # 4 workers, each running back-to-back 5 s requests (0.5 s to first token, then 90 tokens in 4.5 s = 20 tok/s):
    # a worker only generates for 90 % of the time -> 4 x 20 x 0.9 = 72 tok/s aggregate
    records = [rec(start=w * 0.01 + k * 5, first=w * 0.01 + k * 5 + 0.5, end=w * 0.01 + k * 5 + 5.0, tokens=90) for w in range(4) for k in range(4)]
    m = stage_metrics(records, 4, 0.0, 20.0)
    assert m["agg_tps"] == pytest.approx(72.0, rel=0.05) and m["concurrency"] == 4 and m["completed"] == 16 and m["errors"] == 0
    assert m["gen_tps"]["p50"] == pytest.approx(20.0, rel=0.01) and m["ttft_ms"]["p50"] == pytest.approx(500, rel=0.05)
    assert m["upstreams"] == {"m1": 16} and not m["low_samples"] and m["queue_ms"]["p50"] == 0


def test_stage_metrics_errors_queue_and_prompt_speed():
    records = [rec(queue_ms=1000, first=1.5, end=6.5, start=0.0), rec(ok=False, status=429, first=None, end=2.0, tokens=0), rec(ok=False, status="timeout", first=None, tokens=0)]
    m = stage_metrics(records, 2, 0.0, 10.0)
    assert m["started"] == 3 and m["completed"] == 1 and m["errors"] == 2 and m["error_rate"] == pytest.approx(2 / 3)
    assert m["error_breakdown"] == {"429": 1, "timeout": 1} and m["queue_ms"]["p50"] == 1000
    assert m["prompt_tps"]["p50"] == pytest.approx(400 / 0.5)      # 1.5 s to first token minus 1 s queue = 0.5 s of processing
    assert m["server_ttft_ms"]["p50"] == pytest.approx(500) and m["ttft_ms"]["p50"] == pytest.approx(1500) and m["low_samples"]


def test_stage_metrics_empty_stage():
    m = stage_metrics([], 3, 0.0, 10.0)
    assert m["started"] == 0 and m["agg_tps"] == 0 and m["gen_tps"] is None and m["error_rate"] == 0.0 and m["low_samples"]


# ---------------------------------------------------------------- analysis
def stage(c, agg, per, queue=0, err=0.0, completed=20, started=20):
    s = lambda v: {"n": 5, "avg": v, "p50": v, "p95": v, "min": v, "max": v}  # noqa: E731
    return {"concurrency": c, "agg_tps": agg, "gen_tps": s(per), "queue_ms": s(queue), "error_rate": err, "completed": completed, "started": started,
            "errors": int(err * started), "ttft_ms": s(300), "latency_ms": s(5000), "interrupted": False}


def test_analyze_finds_peak_sweet_spot_and_saturation():
    stages = [stage(1, 50, 50), stage(2, 98, 49), stage(4, 190, 47.5), stage(8, 240, 30, queue=800), stage(16, 235, 14.7, queue=3000)]
    a = analyze(stages, slots=4)
    assert a["peak"]["concurrency"] == 8 and a["peak"]["agg_tps"] == 240 and a["single_stream_tps"] == 50
    assert a["recommended_concurrency"] == 4 and a["saturation_concurrency"] == 8 and a["slots"] == 4
    assert any("240.0 tokens/s at 8" in v for v in a["verdict"]) and any("Saturation starts at 8" in v for v in a["verdict"])


def test_analyze_peak_within_tolerance_reports_lowest_concurrency():
    a = analyze([stage(1, 50, 50), stage(2, 98, 49), stage(4, 190, 47), stage(8, 192, 30, queue=800)])   # 8 is only 1% better than 4
    assert a["peak"]["concurrency"] == 4 and a["peak"]["agg_tps"] == 190
    b = analyze([stage(1, 50, 50), stage(2, 98, 49), stage(4, 190, 47), stage(8, 200, 30, queue=800)])   # 5% better: 8 wins
    assert b["peak"]["concurrency"] == 8


def test_analyze_prefers_lower_concurrency_on_equal_peak_and_handles_failures():
    a = analyze([stage(1, 50, 50), stage(2, 100, 50), stage(4, 100, 25, queue=500)])
    assert a["peak"]["concurrency"] == 2 and a["recommended_concurrency"] == 2
    empty = analyze([{**stage(1, 0, 0), "completed": 0}])
    assert empty["peak"] is None and empty["verdict"] and empty["recommended_concurrency"] is None
    assert analyze([stage(1, 50, 50, err=0.5)])["recommended_concurrency"] is None   # error rate above 5 %: not comfortable


def test_should_stop_rules():
    assert should_stop([stage(1, 50, 50)]) is None
    assert "failed" in should_stop([stage(1, 50, 50), {**stage(2, 0, 0), "completed": 0, "started": 5}])
    assert "25%" in should_stop([stage(1, 50, 50), stage(2, 90, 45, err=0.4)])
    flat = [stage(1, 50, 50), stage(2, 100, 50), stage(4, 150, 40), stage(8, 120, 20), stage(16, 100, 10)]
    assert "past the peak" in should_stop(flat)
    assert should_stop(flat[:4]) is None   # a single dip is not enough


# ---------------------------------------------------------------- validation + prompts
def test_validate_config():
    ok = validate_config({"model": "m", "levels": [8, 1, 4, 4], "stage_seconds": 10, "prompt_tokens": 500, "max_tokens": 128})
    assert ok["levels"] == [1, 4, 8] and ok["model"] == "m" and ok["scenario"] == "prose"     # prose is the default
    assert validate_config({"levels": [1], "scenario": "code"})["scenario"] == "code"
    with pytest.raises(ValueError):
        validate_config({"levels": [1], "scenario": "poetry"})
    for bad in ({"levels": []}, {"levels": [0]}, {"levels": [999]}, {"levels": list(range(1, 13))}, {"levels": [1], "stage_seconds": 1},
                {"levels": [1], "prompt_tokens": 1}, {"levels": [1], "max_tokens": 100000}, {"levels": [1], "stage_seconds": 500}):
        with pytest.raises(ValueError):
            validate_config(bad)


def test_make_prompt_is_unique_and_roughly_sized():
    a, b = make_prompt(1000, "aaaa11112222"), make_prompt(1000, "bbbb33334444")
    assert a != b and a[:40] != b[:40]                        # unique from the very first characters (defeats prefix caches)
    assert 3000 < len(a) < 4800 and "write a long" in a
    assert 150 < len(make_prompt(40, "cccc55556666")) < 800


def test_code_scenario_prompts():
    a, b = make_prompt(1000, "aaaa11112222", "code"), make_prompt(1000, "bbbb33334444", "code")
    assert a != b and "aaaa11" in a and "aaaa11" not in b                         # the nonce is inside the identifiers: unique everywhere
    assert 2300 < len(a) < 3900                                                    # ~3 characters per token for code
    assert a.count("def ") + a.count("class ") >= 6 and "Output only code" in a and "import asyncio" in a
    assert "write a long" not in a and "background material" not in a             # no prose boilerplate
    compile(make_prompt(300, "cccc55556666", "code").split("# Task:")[0], "<ctx>", "exec")   # the context really is valid Python
    prose = make_prompt(1000, "aaaa11112222", "prose")
    assert "def " not in prose and prose == make_prompt(1000, "aaaa11112222")     # default scenario is prose, unchanged
    assert make_prompt(100, "dddd77778888", "code") != make_prompt(100, "dddd77778888", "prose")
