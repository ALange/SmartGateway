import csv
import io
import json
import time
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from .. import retention
from ..db import get_session
from ..models import AuditEvent, RequestLog

router = APIRouter(prefix="/api")

# columns exported from the request log (large JSON blobs for compression analysis are left out)
LOG_COLS = ["id", "ts", "gateway_id", "key_id", "public_model", "endpoint_id", "model_id", "upstream_model", "route", "status", "ok",
            "stream", "cached", "queue_ms", "ttft_ms", "total_ms", "prompt_tokens", "completion_tokens", "attempts", "error",
            "finish_reason", "outcome", "cache_state", "compress_mode", "tokens_saved", "guardrail_action", "output_retries"]
AUDIT_COLS = ["id", "ts", "actor", "ip", "action", "target_type", "target_id", "method", "path", "status", "fields", "summary"]


def _stream(rows: Iterator, cols: list[str], fmt: str, name: str) -> StreamingResponse:
    def gen():
        if fmt == "csv":
            buf = io.StringIO(); w = csv.writer(buf)
            w.writerow(cols); yield buf.getvalue()
            for r in rows:
                buf.seek(0); buf.truncate()
                w.writerow([json.dumps(getattr(r, c)) if isinstance(getattr(r, c), (list, dict)) else getattr(r, c) for c in cols])
                yield buf.getvalue()
        else:
            for r in rows:
                yield json.dumps({c: getattr(r, c) for c in cols}, default=str) + "\n"
    ext = "csv" if fmt == "csv" else "jsonl"
    return StreamingResponse(gen(), media_type="text/csv" if fmt == "csv" else "application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{name}-{int(time.time())}.{ext}"'})


def _chunks(s: Session, model, conds: list, batch: int = 1000):
    """Keyset pagination over id so a large export never loads the table into memory."""
    last = 0
    while True:
        q = select(model).where(model.id > last, *conds).order_by(model.id).limit(batch)
        rows = s.exec(q).all()
        if not rows:
            return
        for r in rows:
            yield r
        last = rows[-1].id
        s.expunge_all()


@router.get("/logs/export")
def export_logs(format: str = Query("csv", pattern="^(csv|jsonl)$"), since: Optional[float] = None, until: Optional[float] = None,
                gateway_id: Optional[int] = None, outcome: Optional[str] = None, only_errors: bool = False,
                s: Session = Depends(get_session)):
    conds = []
    if since is not None: conds.append(RequestLog.ts >= since)
    if until is not None: conds.append(RequestLog.ts <= until)
    if gateway_id: conds.append(RequestLog.gateway_id == gateway_id)
    if outcome: conds.append(RequestLog.outcome == outcome)
    if only_errors: conds.append(RequestLog.ok == False)  # noqa: E712
    return _stream(_chunks(s, RequestLog, conds), LOG_COLS, format, "request-log")


def _audit_conds(since, until, action, target_type, actor):
    conds = []
    if since is not None: conds.append(AuditEvent.ts >= since)
    if until is not None: conds.append(AuditEvent.ts <= until)
    if action: conds.append(AuditEvent.action.contains(action))
    if target_type: conds.append(AuditEvent.target_type == target_type)
    if actor: conds.append(AuditEvent.actor == actor)
    return conds


@router.get("/audit")
def audit_list(limit: int = Query(100, le=500), before_id: Optional[int] = None, since: Optional[float] = None, until: Optional[float] = None,
               action: Optional[str] = None, target_type: Optional[str] = None, actor: Optional[str] = None, s: Session = Depends(get_session)):
    q = select(AuditEvent).where(*_audit_conds(since, until, action, target_type, actor)).order_by(AuditEvent.id.desc()).limit(limit)
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    return [r.model_dump() for r in s.exec(q).all()]


@router.get("/audit/export")
def audit_export(format: str = Query("csv", pattern="^(csv|jsonl)$"), since: Optional[float] = None, until: Optional[float] = None,
                 action: Optional[str] = None, target_type: Optional[str] = None, actor: Optional[str] = None, s: Session = Depends(get_session)):
    return _stream(_chunks(s, AuditEvent, _audit_conds(since, until, action, target_type, actor)), AUDIT_COLS, format, "audit-log")


@router.get("/maintenance/db")
def db_info():
    return retention.db_info()


@router.post("/maintenance/prune")
def prune_now():
    return {"deleted": retention.prune_once(), **retention.db_info()}


@router.post("/maintenance/vacuum")
def vacuum():
    before = retention.db_info()["size_bytes"]
    try:
        retention.vacuum()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"VACUUM failed: {e}")
    return {"before": before, "after": retention.db_info()["size_bytes"]}
