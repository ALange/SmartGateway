"""Admin API for the unified endpoint (which gateways are exposed through /unified/v1)."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from .. import settings_store as st
from .. import unified
from ..db import session
from ..models import Endpoint, Gateway, GatewayModel, Model

router = APIRouter(prefix="/api")


class Selection(BaseModel):
    id: int
    model: Optional[str] = None  # default public model for the bare gateway name (None = the gateway's first model)


class KeyIn(BaseModel):
    name: str


class ModelSel(BaseModel):
    model_id: int
    name: Optional[str] = None   # public name on the unified endpoint (None = the upstream model id)


class UnifiedIn(BaseModel):
    enabled: Optional[bool] = None
    gateways: Optional[list[Selection]] = None
    models: Optional[list[ModelSel]] = None


def _load():
    with session() as s:
        gws = [g for g in s.exec(select(Gateway).order_by(Gateway.id)).all() if g.slug not in unified.HIDDEN_SLUGS]
        models: dict[int, list[str]] = {}
        for gm in s.exec(select(GatewayModel).order_by(GatewayModel.id)).all():
            models.setdefault(gm.gateway_id, []).append(gm.public_name)
    return gws, models


def _direct_models(cfg: dict, aliases: list[dict]) -> list[dict]:
    """The upstream models exposed directly, with what the UI needs to show them."""
    names = {a["model"]: a["alias"] for a in aliases if a.get("direct")}
    out = []
    with session() as s:
        eps = {e.id: e.name for e in s.exec(select(Endpoint)).all()}
        for e in cfg["unified_models"]:
            m = s.get(Model, e["model_id"])
            if m:
                out.append({"model_id": m.id, "name": e["name"], "alias": names.get(e["name"]), "upstream_id": m.upstream_id, "endpoint": eps.get(m.endpoint_id, "?"),
                            "capabilities": m.capabilities or [], "enabled": bool(m.enabled)})
    return out


def view() -> dict:
    cfg = st.get_all()
    picked = {g["gateway_id"]: g.get("model") for g in cfg["unified_gateways"]}
    gws, models = _load()
    aliases = unified.exposed(cfg)
    names: dict[int, list[str]] = {}
    for a in aliases:
        names.setdefault(a["gateway"].id, []).append(a["alias"])
    return {
        "enabled": cfg["unified_enabled"],
        "path": "/unified/v1",
        "gateways": [{
            "id": g.id, "name": g.name, "slug": g.slug, "models": models.get(g.id, []),
            "has_keys": unified.has_keys(g.id), "exposed": g.id in picked,
            "model": picked.get(g.id) if picked.get(g.id) in models.get(g.id, []) else (models.get(g.id) or [None])[0],
            "names": names.get(g.id, []),  # model names clients can use for this gateway
        } for g in gws],
        "model_names": [a["alias"] for a in aliases],
        "models": _direct_models(cfg, aliases),
        "keys": [{"id": k["id"], "name": k["name"], "prefix": k["prefix"], "created_at": k["created_at"]} for k in cfg["unified_keys"]],
    }


@router.get("/unified")
def get_unified():
    return view()


@router.put("/unified")
def put_unified(body: UnifiedIn):
    vals: dict = {}
    if body.enabled is not None:
        vals["unified_enabled"] = body.enabled
    if body.gateways is not None:
        gws, models = _load()
        by_id = {g.id: g for g in gws}
        seen, cfg = set(), []
        for sel in body.gateways:
            if sel.id not in by_id:   # (the hidden internal gateway is not in by_id either)
                raise HTTPException(400, f"Gateway {sel.id} does not exist")
            if sel.id in seen:
                raise HTTPException(400, f"Gateway '{by_id[sel.id].name}' is selected twice")
            if sel.model and sel.model not in models.get(sel.id, []):
                raise HTTPException(400, f"Gateway '{by_id[sel.id].name}' has no model '{sel.model}'")
            seen.add(sel.id)
            cfg.append({"gateway_id": sel.id, "model": sel.model or None})
        vals["unified_gateways"] = cfg
    if body.models is not None:
        entries, names = [], set()
        with session() as s:
            for sel in body.models:
                m = s.get(Model, sel.model_id)
                if not m:
                    raise HTTPException(400, f"Model {sel.model_id} does not exist")
                name = (sel.name or m.upstream_id).strip()
                if not name or len(name) > 120:
                    raise HTTPException(400, "A model name must be 1-120 characters")
                if name.lower() in names:
                    raise HTTPException(400, f"The name '{name}' is used twice")
                names.add(name.lower())
                entries.append({"model_id": m.id, "name": name})
        unified.sync_models(entries)
        vals["unified_models"] = entries
    if vals:
        st.update(vals)
    return view()


@router.post("/unified/keys")
def create_key(body: KeyIn):
    name = body.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "Give the key a name (1-60 characters)")
    rec, plain = unified.make_key(name)
    st.update({"unified_keys": [*st.get_all()["unified_keys"], rec]})
    return {"id": rec["id"], "name": name, "prefix": rec["prefix"], "created_at": rec["created_at"], "key": plain}  # the only time the plaintext is returned


@router.delete("/unified/keys/{key_id}")
def delete_key(key_id: str):
    keys = st.get_all()["unified_keys"]
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(404, "Key not found")
    st.update({"unified_keys": [k for k in keys if k["id"] != key_id]})
    return {"ok": True}
