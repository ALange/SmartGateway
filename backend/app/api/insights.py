import statistics
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlmodel import Session, select

from ..compression import pipeline
from ..compression.insights import build_compression_response, build_context_response
from ..db import get_session
from ..models import CompressionEvent, McpCallLog, McpEndpoint, McpGateway, McpItem, RequestLog

router = APIRouter(prefix="/api/insights")
RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
SCAN_LIMIT = 5000


def _since(range_: str) -> float:
    if range_ not in RANGES:
        raise HTTPException(400, f"range must be one of {list(RANGES)}")
    return time.time() - RANGES[range_]


def _group(s: Session, col, since: float, gw: Optional[int]) -> list[dict]:
    fresh = CompressionEvent.cache_hit == False  # noqa: E712
    q = select(col, func.count(), func.sum(case((CompressionEvent.cache_hit == True, 1), else_=0)),  # noqa: E712
               func.sum(case((fresh, CompressionEvent.tokens_before - CompressionEvent.tokens_after), else_=0)),
               func.sum(case((fresh & CompressionEvent.compression_ratio.is_not(None), CompressionEvent.compression_ratio), else_=0)),
               func.sum(case((fresh & CompressionEvent.compression_ratio.is_not(None), 1), else_=0))).where(CompressionEvent.ts >= since).group_by(col)
    if gw:
        q = q.where(CompressionEvent.gateway_id == gw)
    return [{"key": k, "events": int(n or 0), "cache_hit_events": int(h or 0), "tokens_saved": int(ts or 0),
             "sum_ratio": float(sr or 0), "ratio_count": int(rc or 0)} for k, n, h, ts, sr, rc in s.exec(q).all()]


@router.get("/compression")
def compression_insights(range: str = "24h", gateway_id: Optional[int] = None, s: Session = Depends(get_session)):
    since = _since(range)
    by_type = _group(s, CompressionEvent.content_type, since, gateway_id)
    by_comp = _group(s, CompressionEvent.compressor_used, since, gateway_id)
    ev = [CompressionEvent.ts >= since] + ([CompressionEvent.gateway_id == gateway_id] if gateway_id else [])
    events = int(s.exec(select(func.count()).where(*ev)).one() or 0)
    hits = int(s.exec(select(func.count()).where(*ev, CompressionEvent.cache_hit == True)).one() or 0)  # noqa: E712
    applied = int(s.exec(select(func.count()).where(*ev, CompressionEvent.applied == True)).one() or 0)  # noqa: E712
    fresh = s.exec(select(func.coalesce(func.sum(CompressionEvent.tokens_before - CompressionEvent.tokens_after), 0),
                          func.coalesce(func.sum(CompressionEvent.compression_ratio), 0), func.count(CompressionEvent.compression_ratio))
                   .where(*ev, CompressionEvent.cache_hit == False)).one()  # noqa: E712
    rq = [RequestLog.ts >= since] + ([RequestLog.gateway_id == gateway_id] if gateway_id else [])
    requests = int(s.exec(select(func.count()).where(*rq)).one() or 0)
    not_sent = int(s.exec(select(func.coalesce(func.sum(RequestLog.tokens_saved), 0)).where(*rq, RequestLog.compress_mode == "on")).one() or 0)
    potential = int(s.exec(select(func.coalesce(func.sum(RequestLog.tokens_saved), 0)).where(*rq, RequestLog.compress_mode == "shadow")).one() or 0)
    avg_al = s.exec(select(func.avg(RequestLog.alignment_score)).where(*rq, RequestLog.alignment_score.is_not(None))).one()
    # alignment details
    buckets = {"90-100": 0, "70-89": 0, "40-69": 0, "0-39": 0}
    for sc in s.exec(select(RequestLog.alignment_score).where(*rq, RequestLog.alignment_score.is_not(None)).limit(SCAN_LIMIT)).all():
        buckets["90-100" if sc >= 90 else "70-89" if sc >= 70 else "40-69" if sc >= 40 else "0-39"] += 1
    labels: dict[str, int] = {}
    with_findings = 0
    for lab in s.exec(select(RequestLog.volatile_labels).where(*rq, RequestLog.volatile_findings > 0).limit(SCAN_LIMIT)).all():
        with_findings += 1
        for k, v in (lab or {}).items():
            labels[k] = labels.get(k, 0) + int(v)
    variants = [{"public_model": pm, "variants": int(v), "requests": int(n)} for pm, v, n in s.exec(
        select(RequestLog.public_model, func.count(func.distinct(RequestLog.prefix_hash)), func.count()).where(*rq, RequestLog.prefix_hash.is_not(None)).group_by(RequestLog.public_model)).all()]
    return build_compression_response(
        range_=range, requests=requests, events=events, cache_hit_events=hits, applied_events=applied,
        fresh_tokens_saved=int(fresh[0] or 0), sum_ratio=float(fresh[1] or 0), ratio_count=int(fresh[2] or 0),
        tokens_not_sent=not_sent, potential_not_sent=potential, avg_alignment=float(avg_al) if avg_al is not None else None,
        by_content_type=by_type, by_compressor=by_comp,
        alignment={"avgScore": float(avg_al) if avg_al is not None else None, "scoreBuckets": buckets, "volatileLabels": labels,
                   "requestsWithFindings": with_findings, "promptVariants": variants},
        live_cache=pipeline.get_cache().stats())


@router.get("/context")
def context_insights(range: str = "24h", gateway_id: Optional[int] = None, top: int = 10, session_gap: int = 30, s: Session = Depends(get_session)):
    since = _since(range)
    top = max(1, min(50, top))
    q = select(RequestLog).where(RequestLog.ts >= since, RequestLog.ctx_system_chars.is_not(None)).order_by(RequestLog.id.desc()).limit(SCAN_LIMIT + 1)
    if gateway_id:
        q = q.where(RequestLog.gateway_id == gateway_id)
    rows = s.exec(q).all()
    truncated = len(rows) > SCAN_LIMIT
    data = [r.model_dump(include={"id", "ts", "session_key", "prompt_tokens", "completion_tokens", "ctx_system_chars", "ctx_tools_chars", "ctx_messages_chars", "ctx_blocks"}) for r in rows[:SCAN_LIMIT]]
    return build_context_response(data, range_=range, top=top, session_gap_minutes=max(1, session_gap), truncated=truncated)


@router.get("/performance")
async def performance(minutes: int = 60, s: Session = Depends(get_session)):
    """Generation / prompt-processing speed and cache effectiveness per gateway and per upstream model."""
    from ..models import Endpoint, Gateway, Model
    from ..perf_insights import build_performance
    from ..router.cache import cache as cache_facade

    minutes = max(1, min(minutes, 43200))
    until = time.time()
    since = until - minutes * 60
    rows = s.exec(select(RequestLog).where(RequestLog.ts >= since).order_by(RequestLog.id.desc()).limit(20001)).all()
    truncated = len(rows) > 20000
    cols = {"ts", "gateway_id", "model_id", "ok", "stream", "cached", "cache_state", "attempts", "prompt_tokens", "completion_tokens", "ttft_ms", "total_ms", "queue_ms"}
    data = [r.model_dump(include=cols) for r in rows[:20000]]
    gateways = {g.id: g.name for g in s.exec(select(Gateway)).all()}
    eps = {e.id: e.name for e in s.exec(select(Endpoint)).all()}
    models = {m.id: {"upstream": m.upstream_id, "endpoint": eps.get(m.endpoint_id, "")} for m in s.exec(select(Model)).all()}
    out = build_performance(data, gateways, models, since=since, until=until, truncated=truncated)
    out["cache_live"] = await cache_facade.stats()
    return out


@router.get("/mcp")
def mcp_stats(minutes: int = 60, buckets: int = 24, s: Session = Depends(get_session)):
    """MCP endpoint usage for the dashboard: totals, per endpoint, per gateway, top tools and a timeline."""
    from ..mcp import registry
    minutes = max(1, min(minutes, 43200))
    until = time.time()
    since = until - minutes * 60
    rows = s.exec(select(McpCallLog).where(McpCallLog.ts >= since).order_by(McpCallLog.id.desc()).limit(50001)).all()
    truncated = len(rows) > 50000
    rows = rows[:50000]
    eps = {e.id: e for e in s.exec(select(McpEndpoint)).all()}
    gws = {g.id: g.name for g in s.exec(select(McpGateway)).all()}
    counts: dict = {}
    for kind, ep_id, n in s.exec(select(McpItem.kind, McpItem.endpoint_id, func.count()).group_by(McpItem.kind, McpItem.endpoint_id)).all():
        counts.setdefault(ep_id, {})[kind] = n

    def block(rs: list) -> dict:
        ms = sorted(r.total_ms for r in rs)
        return {"calls": len(rs), "errors": sum(1 for r in rs if not r.ok),
                "avg_ms": int(sum(ms) / len(ms)) if ms else None, "p50_ms": ms[len(ms) // 2] if ms else None, "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None}

    by_ep: dict = {}
    by_gw: dict = {}
    by_tool: dict = {}
    for r in rows:
        by_ep.setdefault(r.endpoint_id, []).append(r)
        by_gw.setdefault(r.gateway_id, []).append(r)
        if r.method == "tools/call":
            by_tool.setdefault((r.endpoint_id, r.name), []).append(r)
    endpoints = [{"id": e.id, "name": e.name, "status": registry.status.get(e.id, e.status), "tools": counts.get(e.id, {}).get("tool", 0),
                  "resources": counts.get(e.id, {}).get("resource", 0) + counts.get(e.id, {}).get("template", 0), "prompts": counts.get(e.id, {}).get("prompt", 0),
                  **block(by_ep.get(e.id, []))} for e in eps.values()]
    endpoints.sort(key=lambda e: -e["calls"])
    gateways = sorted(({"id": g, "name": gws.get(g, f"#{g} (deleted)"), **block(rs)} for g, rs in by_gw.items()), key=lambda x: -x["calls"])
    tools = sorted(({"name": n, "endpoint": eps[e].name if e in eps else "?", **block(rs)} for (e, n), rs in by_tool.items()), key=lambda x: -x["calls"])[:10]
    step = (until - since) / buckets
    line = [{"t": since + i * step, "calls": 0, "errors": 0} for i in range(buckets)]
    for r in rows:
        i = min(buckets - 1, int((r.ts - since) / step))
        line[i]["calls"] += 1
        line[i]["errors"] += 0 if r.ok else 1
    return {"meta": {"since": since, "until": until, "truncated": truncated}, "totals": {**block(rows), "endpoints": len(eps), "online": sum(1 for e in endpoints if e["status"] == "online")},
            "endpoints": endpoints, "gateways": gateways, "tools": tools, "timeline": line}


@router.get("/live-series")
def live_series(minutes: int = 30, buckets: int = 30, gateway_id: Optional[int] = None, gateway_ids: Optional[str] = None,
                s: Session = Depends(get_session)):
    """Time series for a gateway's live view: usage per bucket, and generation / prompt-processing speed per upstream model."""
    from ..models import Endpoint, Model
    from ..perf_insights import gen_speed, prompt_speed, summarize

    minutes = max(1, min(minutes, 1440))
    buckets = max(5, min(buckets, 120))
    until = time.time()
    since = until - minutes * 60
    step = (until - since) / buckets
    q = select(RequestLog).where(RequestLog.ts >= since)
    if gateway_id:
        q = q.where(RequestLog.gateway_id == gateway_id)
    if gateway_ids is not None:   # comma-separated, e.g. the gateways behind the unified endpoint
        q = q.where(RequestLog.gateway_id.in_([int(x) for x in gateway_ids.split(",") if x.strip().isdigit()]))
    cols = {"ts", "model_id", "ok", "stream", "cached", "attempts", "prompt_tokens", "completion_tokens", "ttft_ms", "total_ms", "queue_ms"}
    rows = [r.model_dump(include=cols) for r in s.exec(q.order_by(RequestLog.id.desc()).limit(20000)).all()]

    usage = {k: [0] * buckets for k in ("requests", "errors", "cached", "prompt_tokens", "completion_tokens")}
    per: dict[int, dict] = {}
    for r in rows:
        b = min(buckets - 1, int((r["ts"] - since) / step))
        usage["requests"][b] += 1
        usage["errors"][b] += 0 if r["ok"] else 1
        usage["cached"][b] += 1 if r["cached"] else 0
        usage["prompt_tokens"][b] += r["prompt_tokens"] or 0
        usage["completion_tokens"][b] += r["completion_tokens"] or 0
        if r["model_id"] and not r["cached"]:
            d = per.setdefault(r["model_id"], {"requests": 0, "gen": [[] for _ in range(buckets)], "prompt": [[] for _ in range(buckets)]})
            d["requests"] += 1
            g, p = gen_speed(r), prompt_speed(r)
            if g is not None:
                d["gen"][b].append(g)
            if p is not None:
                d["prompt"][b].append(p)
    eps = {e.id: e.name for e in s.exec(select(Endpoint)).all()}
    info = {m.id: m for m in s.exec(select(Model).where(Model.id.in_(list(per) or [0]))).all()}

    def med(v):
        return round(statistics.median(v), 1) if v else None
    models = []
    for mid in sorted(per):
        d, m = per[mid], info.get(mid)
        gen_all = [x for b in d["gen"] for x in b]
        pp_all = [x for b in d["prompt"] for x in b]
        models.append({"model_id": mid, "upstream": m.upstream_id if m else f"#{mid}", "endpoint": eps.get(m.endpoint_id, "") if m else "",
                       "requests": d["requests"], "gen": [med(b) for b in d["gen"]], "prompt": [med(b) for b in d["prompt"]],
                       "gen_stats": summarize(gen_all), "prompt_stats": summarize(pp_all)})
    return {"since": since, "until": until, "bucket_seconds": step,
            "starts": [round(since + i * step, 1) for i in range(buckets)], "usage": usage, "models": models}
