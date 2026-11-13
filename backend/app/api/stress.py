from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import stress, stress_metrics as sm
from ..db import session
from ..models import Gateway, GatewayModel, StressRun
from ..proxy import estimate_tokens, load_members

router = APIRouter(prefix="/api")


class StressIn(BaseModel):
    model: Optional[str] = None
    preset: Optional[str] = None  # quick | standard | thorough (fills levels / stage_seconds unless given)
    levels: Optional[list[int]] = None
    stage_seconds: Optional[float] = None
    scenario: str = "prose"  # prose | code
    prompt_tokens: int = 500
    max_tokens: int = 128
    acknowledge: bool = False  # the caller confirms that real load will be sent to the upstream servers


@router.post("/gateways/{gw_id}/stress")
async def start_stress(gw_id: int, body: StressIn):
    if not body.acknowledge:
        raise HTTPException(400, "A stress test sends real load to your upstream servers. Confirm with acknowledge=true.")
    with session() as s:
        gw = s.get(Gateway, gw_id)
        if not gw:
            raise HTTPException(404, "Gateway not found")
        gms = s.exec(select(GatewayModel).where(GatewayModel.gateway_id == gw_id)).all()
        s.expunge(gw)
    if not gms:
        raise HTTPException(400, "This gateway has no models")
    model = body.model or gms[0].public_name
    gm = next((g for g in gms if g.public_name == model), None)
    if not gm:
        raise HTTPException(404, f"Model '{model}' not found on this gateway")
    preset = sm.PRESETS.get(body.preset or "standard", sm.PRESETS["standard"])
    try:
        cfg = sm.validate_config({"model": model, "levels": body.levels or preset["levels"], "stage_seconds": body.stage_seconds or preset["stage_seconds"],
                                  "prompt_tokens": body.prompt_tokens, "max_tokens": body.max_tokens, "scenario": body.scenario})
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    members = load_members(gm.id)
    if not members:
        raise HTTPException(400, "This model has no enabled members")
    need = cfg["prompt_tokens"] + cfg["max_tokens"]
    if all(m.context_length and m.context_length < need for m in members):
        raise HTTPException(400, f"Prompt + output (~{need} tokens) exceeds the context window of every member (largest {max(m.context_length or 0 for m in members)})")
    cur = stress.running()
    if cur:
        raise HTTPException(409, {"message": f"A stress test is already running on '{cur.gateway.name}'", "job_id": cur.id})
    slots = sum({m.model_id: m.limit for m in members}.values())
    job = stress.start(gw, cfg, slots)
    return {"job_id": job.id, "config": cfg, "slots": slots, "estimate_seconds": len(cfg["levels"]) * (cfg["stage_seconds"] + 3)}


@router.get("/stress/current")
def current():
    j = stress.running()
    return j.snapshot() if j else None


@router.get("/stress/presets")
def presets():
    return sm.PRESETS


@router.get("/stress/runs/{run_id}")
def get_run(run_id: int):
    with session() as s:
        r = s.get(StressRun, run_id)
        if not r:
            raise HTTPException(404)
        return r.model_dump()


@router.delete("/stress/runs/{run_id}")
def delete_run(run_id: int):
    with session() as s:
        r = s.get(StressRun, run_id)
        if not r:
            raise HTTPException(404)
        s.delete(r)
        s.commit()
    return {"ok": True}


@router.get("/stress/{job_id}")
def get_job(job_id: str):
    j = stress.JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown stress test (the server may have restarted)")
    return j.snapshot()


@router.post("/stress/{job_id}/stop")
def stop_job(job_id: str):
    j = stress.JOBS.get(job_id)
    if not j:
        raise HTTPException(404)
    if j.status == "running":
        stress.stop(j)
    return {"ok": True}


@router.get("/gateways/{gw_id}/stress/runs")
def runs(gw_id: int, limit: int = 10):
    with session() as s:
        rows = s.exec(select(StressRun).where(StressRun.gateway_id == gw_id).order_by(StressRun.started_at.desc()).limit(max(1, min(limit, 50)))).all()
        return [{"id": r.id, "started_at": r.started_at, "finished_at": r.finished_at, "status": r.status, "stop_reason": r.stop_reason,
                 "model": r.public_model, "config": r.config, "summary": (r.result or {}).get("summary")} for r in rows]
