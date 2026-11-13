"""Pure maths for gateway stress tests: stage metrics, peak/saturation analysis, config validation. No I/O."""
from collections import Counter
from typing import Optional

from .perf_insights import MIN_COMPLETION_TOKENS, MIN_GEN_SECONDS, MIN_PROMPT_TOKENS, MIN_TTFT_MS, summarize

SCENARIOS = ("prose", "code")
MAX_LEVELS = 10
MAX_CONCURRENCY = 128
MAX_TOTAL_SECONDS = 30 * 60
PRESETS = {
    "quick": {"levels": [1, 2, 4, 8], "stage_seconds": 10},
    "standard": {"levels": [1, 2, 4, 8, 16], "stage_seconds": 15},
    "thorough": {"levels": [1, 2, 4, 8, 16, 32, 64], "stage_seconds": 20},
}
SWEET_SPOT_RATIO = 0.7      # a level is "comfortable" while each request still gets >= 70 % of single-stream speed
QUEUE_MS_LIMIT = 50         # ... and requests do not wait for a slot
MAX_ERROR_RATE = 0.05
STOP_ERROR_RATE = 0.25
PAST_PEAK_RATIO = 0.9
PEAK_TOLERANCE = 0.98       # stages within 2% of the best throughput count as equally good: report the lowest concurrency among them


def validate_config(cfg: dict) -> dict:
    """Return a cleaned config or raise ValueError with a user-facing message."""
    levels = sorted({int(x) for x in cfg.get("levels") or []})
    if not levels or any(x < 1 or x > MAX_CONCURRENCY for x in levels):
        raise ValueError(f"Concurrency levels must be between 1 and {MAX_CONCURRENCY}")
    if len(levels) > MAX_LEVELS:
        raise ValueError(f"At most {MAX_LEVELS} concurrency levels")
    stage = float(cfg.get("stage_seconds", 15))
    if not 5 <= stage <= 120:
        raise ValueError("Stage duration must be between 5 and 120 seconds")
    prompt = int(cfg.get("prompt_tokens", 500))
    if not 16 <= prompt <= 32000:
        raise ValueError("Prompt size must be between 16 and 32000 tokens")
    scenario = str(cfg.get("scenario") or "prose")
    if scenario not in SCENARIOS:
        raise ValueError(f"Scenario must be one of {', '.join(SCENARIOS)}")
    out = int(cfg.get("max_tokens", 128))
    if not 8 <= out <= 4096:
        raise ValueError("Max output tokens must be between 8 and 4096")
    total = len(levels) * (stage + 5)
    if total > MAX_TOTAL_SECONDS:
        raise ValueError(f"The test would take about {int(total // 60)} minutes; the limit is {MAX_TOTAL_SECONDS // 60}")
    return {"model": str(cfg.get("model") or ""), "levels": levels, "stage_seconds": stage, "prompt_tokens": prompt, "max_tokens": out, "scenario": scenario}


def integrate_tokens(records: list[dict], w0: float, w1: float) -> float:
    """Tokens generated inside [w0, w1]: each request's tokens are spread evenly over first-token..end (steady-state estimate)."""
    total = 0.0
    for r in records:
        a, b, n = r.get("first"), r.get("end"), r.get("tokens") or 0
        if a is None or b is None or not n or not r.get("ok"):
            continue
        if b <= a:
            total += n if w0 <= a <= w1 else 0
            continue
        total += n * max(0.0, min(b, w1) - max(a, w0)) / (b - a)
    return total


def _req_gen_tps(r: dict) -> Optional[float]:
    if not (r.get("ok") and r.get("first") is not None and r.get("end") is not None):
        return None
    dt, n = r["end"] - r["first"], r.get("tokens") or 0
    return n / dt if (n >= MIN_COMPLETION_TOKENS and dt >= MIN_GEN_SECONDS) else None


def _req_prompt_tps(r: dict) -> Optional[float]:
    if not (r.get("ok") and r.get("first") is not None):
        return None
    dt = (r["first"] - r["start"]) * 1000 - (r.get("queue_ms") or 0)
    pt = r.get("prompt_tokens") or 0
    return pt / (dt / 1000) if (dt >= MIN_TTFT_MS and pt >= MIN_PROMPT_TOKENS) else None


def stage_metrics(records: list[dict], concurrency: int, t0: float, stage_seconds: float, interrupted: bool = False) -> dict:
    warm = min(3.0, stage_seconds * 0.15)
    w0, w1 = t0 + warm, t0 + stage_seconds
    ok = [r for r in records if r.get("ok")]
    bad = [r for r in records if not r.get("ok")]
    window = max(0.001, w1 - w0)
    ttft = [(r["first"] - r["start"]) * 1000 for r in ok if r.get("first") is not None]
    server_ttft = [max(0.0, (r["first"] - r["start"]) * 1000 - (r.get("queue_ms") or 0)) for r in ok if r.get("first") is not None]
    lat = [(r["end"] - r["start"]) * 1000 for r in ok if r.get("end") is not None]
    done_in_window = sum(1 for r in ok if r.get("end") is not None and w0 <= r["end"] <= w1)
    return {
        "concurrency": concurrency, "interrupted": interrupted,
        "started": len(records), "completed": len(ok), "errors": len(bad),
        "error_rate": (len(bad) / len(records)) if records else 0.0,
        "error_breakdown": dict(Counter(str(r.get("status") or r.get("error") or "error")[:40] for r in bad)),
        "req_per_s": done_in_window / window,
        "agg_tps": integrate_tokens(records, w0, w1) / window,
        "tokens_out": sum(r.get("tokens") or 0 for r in ok),
        "gen_tps": summarize([v for v in (_req_gen_tps(r) for r in records) if v is not None]),
        "prompt_tps": summarize([v for v in (_req_prompt_tps(r) for r in records) if v is not None]),
        "ttft_ms": summarize(ttft), "server_ttft_ms": summarize(server_ttft), "latency_ms": summarize(lat),
        "queue_ms": summarize([float(r.get("queue_ms") or 0) for r in ok]),
        "upstreams": dict(Counter(r.get("upstream") for r in ok if r.get("upstream"))),
        "low_samples": len(ok) < max(3, concurrency),
    }


def should_stop(stages: list[dict]) -> Optional[str]:
    """Early-stop rule evaluated after each stage; returns a reason or None."""
    last = stages[-1]
    if last["started"] and last["completed"] == 0:
        return "every request in the last stage failed"
    if last["error_rate"] > STOP_ERROR_RATE:
        return f"more than {int(STOP_ERROR_RATE * 100)}% of requests failed at concurrency {last['concurrency']}"
    if len(stages) >= 3:
        best = max(s["agg_tps"] for s in stages)
        if best > 0 and all(s["agg_tps"] < PAST_PEAK_RATIO * best for s in stages[-2:]):
            return "throughput stopped improving (past the peak)"
    return None


def analyze(stages: list[dict], slots: Optional[int] = None) -> dict:
    """Peak throughput, single-stream speed, comfortable concurrency and the saturation point."""
    good = [s for s in stages if s["completed"] > 0 and s["agg_tps"] > 0]
    if not good:
        return {"peak": None, "single_stream_tps": None, "recommended_concurrency": None, "saturation_concurrency": None, "slots": slots,
                "verdict": ["No stage completed successfully, so no performance figures could be measured."]}
    best = max(s["agg_tps"] for s in good)
    peak = min((s for s in good if s["agg_tps"] >= PEAK_TOLERANCE * best), key=lambda s: s["concurrency"])
    base = good[0]
    single = base["gen_tps"]["p50"] if base["gen_tps"] else None
    if single is None:
        single = base["agg_tps"] / base["concurrency"] if base["concurrency"] else None

    def comfortable(s):
        per = s["gen_tps"]["p50"] if s["gen_tps"] else (s["agg_tps"] / s["concurrency"])
        q = s["queue_ms"]["p50"] if s["queue_ms"] else 0
        return single and per >= SWEET_SPOT_RATIO * single and q <= QUEUE_MS_LIMIT and s["error_rate"] <= MAX_ERROR_RATE

    sweet = [s for s in good if comfortable(s)]
    recommended = max(s["concurrency"] for s in sweet) if sweet else None
    sat = next((s["concurrency"] for s in good if not comfortable(s)), None)
    verdict = [f"Peak throughput: {peak['agg_tps']:.1f} tokens/s at {peak['concurrency']} concurrent request{'s' if peak['concurrency'] != 1 else ''}."]
    if single:
        verdict.append(f"A single request generates about {single:.1f} tokens/s.")
        if peak["concurrency"] > 0:
            verdict.append(f"At the peak the gateway delivers {peak['agg_tps'] / single:.1f}× the single-stream throughput "
                           f"({peak['agg_tps'] / (single * peak['concurrency']) * 100:.0f}% scaling efficiency).")
    if recommended:
        verdict.append(f"Up to {recommended} concurrent request{'s' if recommended != 1 else ''} each request keeps at least {int(SWEET_SPOT_RATIO * 100)}% of its single-stream speed without queueing.")
    if sat:
        s = next(x for x in good if x["concurrency"] == sat)
        q = s["queue_ms"]["p50"] if s["queue_ms"] else 0
        verdict.append(f"Saturation starts at {sat} concurrent requests" + (f" (median queue wait {q:.0f} ms)." if q > QUEUE_MS_LIMIT else "."))
    if slots:
        verdict.append(f"Configured upstream slots for this model: {slots}.")
    errs = sum(s["errors"] for s in stages)
    if errs:
        verdict.append(f"{errs} request{'s' if errs != 1 else ''} failed during the test.")
    return {"peak": {"concurrency": peak["concurrency"], "agg_tps": peak["agg_tps"], "ttft_ms_p50": peak["ttft_ms"]["p50"] if peak["ttft_ms"] else None,
                     "latency_ms_p95": peak["latency_ms"]["p95"] if peak["latency_ms"] else None},
            "single_stream_tps": single, "recommended_concurrency": recommended, "saturation_concurrency": sat, "slots": slots, "verdict": verdict}
