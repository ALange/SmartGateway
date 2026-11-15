"""Periodic pruning of old rows so the SQLite file does not grow without bound."""
import asyncio
import logging
import os
import time

from sqlalchemy import delete, func, text
from sqlmodel import select

from . import config, settings_store
from .db import engine, session
from .models import AuditEvent, CompressionEvent, CouncilRun, RequestLog, StressRun

log = logging.getLogger("smartgateway.retention")
BATCH = 5000
# setting -> (table, timestamp column)
TARGETS = {"log_retention_days": (RequestLog, "ts"), "compression_retention_days": (CompressionEvent, "ts"),
           "audit_retention_days": (AuditEvent, "ts"), "stress_retention_days": (StressRun, "started_at"),
           "council_retention_days": (CouncilRun, "ts")}


def _prune_table(model, col: str, cutoff: float) -> int:
    total = 0
    c = getattr(model, col)
    while True:
        with session() as s:
            ids = s.exec(select(model.id).where(c < cutoff).limit(BATCH)).all()
            if not ids:
                return total
            s.exec(delete(model).where(model.id.in_(ids)))
            s.commit()
            total += len(ids)


def prune_once(settings: dict | None = None, now: float | None = None) -> dict[str, int]:
    cfg = settings if settings is not None else settings_store.get_all()
    now = now if now is not None else time.time()
    out: dict[str, int] = {}
    for key, (model, col) in TARGETS.items():
        days = int(cfg.get(key) or 0)
        if days > 0:
            out[model.__tablename__] = _prune_table(model, col, now - days * 86400)
    return out


def vacuum() -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))


def db_info() -> dict:
    path = os.path.join(config.DATA_DIR, "smartgateway.db")
    with session() as s:
        counts = {m.__tablename__: s.exec(select(func.count()).select_from(m)).one() for m, _ in TARGETS.values()}
        oldest = s.exec(select(func.min(RequestLog.ts))).one()
    return {"size_bytes": os.path.getsize(path) if os.path.exists(path) else 0, "rows": counts, "oldest_log": oldest}


async def loop(interval: float = 3600.0) -> None:
    while True:
        try:
            res = await asyncio.to_thread(prune_once)
            if any(res.values()):
                log.info("retention pruned %s", res)
        except Exception as e:  # noqa: BLE001
            log.warning("retention failed: %s", e)
        await asyncio.sleep(interval)
