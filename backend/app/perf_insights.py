"""Performance and cache aggregation for the dashboard (pure functions, no DB or HTTP).

Speeds are measured on streaming requests only, because only a stream lets us split a request into its two phases:
  prompt processing = request sent -> first token (TTFT)          => prompt_tokens / TTFT
  generation        = first token -> end of stream (minus queueing) => completion_tokens / generation time
A request is measured when it succeeded on the first attempt, was not answered from the cache, produced enough tokens and
took long enough for the numbers to be meaningful.
"""
import statistics
from collections import defaultdict
from typing import Optional

MIN_COMPLETION_TOKENS = 8
MIN_GEN_SECONDS = 0.2
MIN_PROMPT_TOKENS = 32
MIN_TTFT_MS = 30


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


def summarize(values: list[float]) -> Optional[dict]:
    if not values:
        return None
    return {"n": len(values), "avg": sum(values) / len(values), "p50": statistics.median(values), "p95": pct(values, 0.95),
            "min": min(values), "max": max(values)}


def gen_speed(r: dict) -> Optional[float]:
    """Generation tokens/second of one request, or None if it cannot be measured."""
    if not (r.get("ok") and r.get("stream") and not r.get("cached") and (r.get("attempts") or 1) == 1):
        return None
    ct, ttft, total = r.get("completion_tokens") or 0, r.get("ttft_ms"), r.get("total_ms")
    if ct < MIN_COMPLETION_TOKENS or ttft is None or total is None:
        return None
    gen_s = (total - (r.get("queue_ms") or 0) - ttft) / 1000.0
    return ct / gen_s if gen_s >= MIN_GEN_SECONDS else None


def prompt_speed(r: dict) -> Optional[float]:
    """Prompt-processing tokens/second (prompt tokens over time-to-first-token), or None."""
    if not (r.get("ok") and r.get("stream") and not r.get("cached") and (r.get("attempts") or 1) == 1):
        return None
    pt, ttft = r.get("prompt_tokens") or 0, r.get("ttft_ms")
    return pt / (ttft / 1000.0) if (ttft is not None and ttft >= MIN_TTFT_MS and pt >= MIN_PROMPT_TOKENS) else None


def cache_summary(rows: list[dict]) -> dict:
    hits = [r for r in rows if r.get("cache_state") == "hit" or r.get("cached")]
    tracked_hits = sum(1 for r in rows if r.get("cache_state") == "hit")  # rows written since the outcome was recorded
    misses = sum(1 for r in rows if r.get("cache_state") == "miss" and not r.get("cached"))
    skips = sum(1 for r in rows if r.get("cache_state") == "skip")
    bypass = sum(1 for r in rows if r.get("cache_state") == "bypass")
    eligible = tracked_hits + misses  # the hit rate only uses requests whose outcome (hit or miss) was recorded; older log rows have hits only
    return {"hits": len(hits), "tracked_hits": tracked_hits, "misses": misses, "skipped": skips, "bypassed": bypass, "eligible": eligible,
            "hit_rate": (tracked_hits / eligible) if eligible else None,
            "tokens_saved": sum((r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in hits),
            "stream_hits": sum(1 for r in hits if r.get("stream"))}


def speed_block(rows: list[dict]) -> dict:
    gen = [v for v in (gen_speed(r) for r in rows) if v is not None]
    pp = [v for v in (prompt_speed(r) for r in rows) if v is not None]
    ttft = [r["ttft_ms"] for r in rows if r.get("ok") and not r.get("cached") and r.get("stream") and r.get("ttft_ms") is not None]
    return {"gen_tps": summarize(gen), "prompt_tps": summarize(pp), "ttft_ms": summarize(ttft)}


def build_performance(rows: list[dict], gateways: dict, models: dict, *, since: float, until: float, buckets: int = 24, truncated: bool = False) -> dict:
    """rows: request-log dicts. gateways: id -> name. models: id -> {"upstream", "endpoint"}."""
    by_gw: dict[int, list[dict]] = defaultdict(list)
    by_model: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_gw[r["gateway_id"]].append(r)
        if r.get("model_id") and not r.get("cached"):
            by_model[r["model_id"]].append(r)

    gw_out = []
    for gid, rs in by_gw.items():
        ok = [r for r in rs if r.get("ok")]
        gw_out.append({"gateway_id": gid, "name": gateways.get(gid, f"#{gid} (deleted)"), "requests": len(rs), "errors": len(rs) - len(ok),
                       "streamed": sum(1 for r in rs if r.get("stream")), **speed_block(rs), "cache": cache_summary(rs)})
    gw_out.sort(key=lambda g: -g["requests"])

    model_out = []
    for mid, rs in by_model.items():
        sb = speed_block(rs)
        if sb["gen_tps"] or sb["prompt_tps"]:
            info = models.get(mid, {})
            model_out.append({"model_id": mid, "upstream": info.get("upstream", f"#{mid}"), "endpoint": info.get("endpoint", ""), "requests": len(rs), **sb})
    model_out.sort(key=lambda m: -(m["gen_tps"]["p50"] if m["gen_tps"] else 0))

    span = max(1.0, until - since)
    step = span / buckets
    series = []
    for gw in gw_out[:6]:
        pts = []
        for b in range(buckets):
            lo, hi = since + b * step, since + (b + 1) * step
            vals = [v for v in (gen_speed(r) for r in by_gw[gw["gateway_id"]] if lo <= r["ts"] < hi) if v is not None]
            pts.append(round(statistics.median(vals), 1) if vals else None)
        series.append({"gateway_id": gw["gateway_id"], "name": gw["name"], "points": pts})

    return {"meta": {"since": since, "until": until, "requests": len(rows), "truncated": truncated, "buckets": buckets, "bucket_seconds": step,
                     "notes": {"measured_on": "streaming requests answered by an upstream on the first attempt",
                               "prompt_speed": "prompt tokens / time to first token (includes producing the first token)",
                               "generation_speed": "completion tokens / (time after the first token, queue time excluded)"}},
            "totals": {**speed_block(rows), "cache": cache_summary(rows)}, "gateways": gw_out, "models": model_out,
            "timeline": {"starts": [round(since + b * step, 1) for b in range(buckets)], "series": series}}
