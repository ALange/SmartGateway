import asyncio
import json
import time

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlmodel import Session, select

from .. import events
from ..db import get_session
from ..models import Endpoint, Gateway, Model, RequestLog
from ..router import health
from ..router.scheduler import scheduler

router = APIRouter(prefix="/api")


def live_snapshot(s: Session) -> dict:
    st = scheduler.stats
    models = {}
    for m in s.exec(select(Model)).all():
        models[m.id] = {"in_flight": st.inflight(m.id), "external": st.external_busy.get(m.id, 0), "max_slots": m.max_slots,
                        "ewma_ms": int(st.ewma_latency.get(m.id, 0) * 1000),
                        "breaker": scheduler.breaker.state(m.id)}
    eps = {e.id: {"status": health.status.get(e.id, e.status)} for e in s.exec(select(Endpoint)).all()}
    qd = {g.id: scheduler.queue_depth(g.id) for g in s.exec(select(Gateway)).all()}
    return {"models": models, "endpoints": eps, "queue": qd, "ts": time.time()}


@router.get("/live")
def live(s: Session = Depends(get_session)):
    return live_snapshot(s)


def _pct(vals: list, p: float):
    if not vals:
        return 0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p))]


@router.get("/stats")
def stats(minutes: int = 60, gateway_id: int | None = None, s: Session = Depends(get_session)):
    since = time.time() - minutes * 60
    q = select(RequestLog).where(RequestLog.ts >= since)
    if gateway_id:
        q = q.where(RequestLog.gateway_id == gateway_id)
    rows = s.exec(q).all()
    total = len(rows)
    ok = sum(1 for r in rows if r.ok)
    lat = [r.total_ms for r in rows if r.ok]
    ttft = [r.ttft_ms for r in rows if r.ok and r.ttft_ms is not None]
    by_ep: dict = {}
    by_model: dict = {}
    bucket_n = 20
    span = minutes * 60 / bucket_n
    buckets = [{"t": since + i * span, "requests": 0, "errors": 0, "tokens": 0} for i in range(bucket_n)]
    for r in rows:
        if r.endpoint_id:
            by_ep[r.endpoint_id] = by_ep.get(r.endpoint_id, 0) + 1
        k = r.public_model
        by_model[k] = by_model.get(k, 0) + 1
        i = min(bucket_n - 1, int((r.ts - since) / span))
        buckets[i]["requests"] += 1
        buckets[i]["errors"] += 0 if r.ok else 1
        buckets[i]["tokens"] += r.completion_tokens
    ep_names = {e.id: e.name for e in s.exec(select(Endpoint)).all()}
    per_model = {}
    for r in rows:
        if r.model_id:
            d = per_model.setdefault(r.model_id, {"requests": 0, "tokens": 0})
            d["requests"] += 1
            d["tokens"] += r.completion_tokens
    comp_tokens = sum(r.completion_tokens for r in rows)
    return {
        "minutes": minutes, "total": total, "ok": ok, "errors": total - ok,
        "success_rate": (ok / total) if total else 1.0,
        "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95), "ttft_p50_ms": _pct(ttft, 0.5),
        "prompt_tokens": sum(r.prompt_tokens for r in rows), "completion_tokens": comp_tokens,
        "rps": round(total / (minutes * 60), 3),
        "by_endpoint": [{"endpoint_id": k, "name": ep_names.get(k, str(k)), "requests": v} for k, v in by_ep.items()],
        "by_model": [{"model": k, "requests": v} for k, v in sorted(by_model.items(), key=lambda x: -x[1])],
        "per_model": per_model,
        "timeline": buckets,
    }


@router.get("/logs")
def logs(limit: int = Query(100, le=1000), gateway_id: int | None = None, only_errors: bool = False,
         s: Session = Depends(get_session)):
    q = select(RequestLog).order_by(RequestLog.id.desc()).limit(limit)
    if gateway_id:
        q = q.where(RequestLog.gateway_id == gateway_id)
    if only_errors:
        q = q.where(RequestLog.ok == False)  # noqa: E712
    return [r.model_dump() for r in s.exec(q).all()]


@router.websocket("/ws")
async def ws(sock: WebSocket):
    from ..main import request_authed
    if not request_authed(sock.headers, sock.cookies):
        await sock.close(code=4401)
        return
    await sock.accept()
    q = events.subscribe()
    try:
        from ..db import session
        with session() as s:
            await sock.send_text(json.dumps({"type": "snapshot", **live_snapshot(s)}))

        async def ticker():
            while True:
                await asyncio.sleep(1.0)
                with session() as s2:
                    await sock.send_text(json.dumps({"type": "snapshot", **live_snapshot(s2)}))

        t = asyncio.create_task(ticker())
        try:
            while True:
                ev = await q.get()
                await sock.send_text(json.dumps(ev, default=str))
        finally:
            t.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(q)
