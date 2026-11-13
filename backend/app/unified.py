"""Unified endpoint: several gateways exposed through ONE OpenAI-compatible API (`/unified/v1`).

The model name a client sends is the *gateway name*. A gateway with several public models is reachable as
`<gateway>` (its default model) and, additionally, as `<gateway>/<public model>`. Authentication stays with
each gateway (its own API keys, limits and quotas), so nothing new has to be managed.
"""
import secrets
import time
from typing import Optional

from sqlmodel import select

from . import settings_store as st
from .crypto import hash_key
from .db import session
from .models import ApiKey, Gateway, GatewayModel


def build_aliases(cfg: list[dict], gateways: dict[int, Gateway], models: dict[int, list[str]]) -> list[dict]:
    """Pure: turn the saved selection into the list of model names the unified endpoint serves.

    Each item: {alias, gateway (Gateway), model (public name on that gateway), default (bool)}.
    Selected gateways that no longer exist (or have no models) are skipped; clashing names get the slug appended.
    """
    out: list[dict] = []
    used: set[str] = set()

    def unique(name: str, gw: Gateway) -> str:
        for cand in (name, f"{name} ({gw.slug})", gw.slug):
            if cand.lower() not in used:
                used.add(cand.lower())
                return cand
        return name

    for sel in cfg or []:
        gw = gateways.get(sel.get("gateway_id"))
        names = models.get(sel.get("gateway_id")) or []
        if gw is None or not names:
            continue
        default = sel.get("model") if sel.get("model") in names else names[0]
        base = unique((gw.name or "").strip() or gw.slug, gw)
        out.append({"alias": base, "gateway": gw, "model": default, "default": True})
        if len(names) > 1:
            for m in names:
                out.append({"alias": unique(f"{base}/{m}", gw), "gateway": gw, "model": m, "default": False})
    return out


def exposed(settings: Optional[dict] = None) -> list[dict]:
    cfg = (settings or st.get_cached()).get("unified_gateways") or []
    if not cfg:
        return []
    with session() as s:
        gws = {g.id: g for g in s.exec(select(Gateway)).all()}
        models: dict[int, list[str]] = {}
        for gm in s.exec(select(GatewayModel).order_by(GatewayModel.id)).all():
            models.setdefault(gm.gateway_id, []).append(gm.public_name)
    return build_aliases(cfg, gws, models)


def resolve(wanted: str, aliases: list[dict]) -> Optional[dict]:
    """Exact name, then case-insensitive, then the gateway slug (-> its default model)."""
    w = (wanted or "").strip()
    if not w:
        return None
    for a in aliases:
        if a["alias"] == w:
            return a
    low = w.lower()
    for a in aliases:
        if a["alias"].lower() == low:
            return a
    for a in aliases:
        if a["default"] and a["gateway"].slug.lower() == low:
            return a
    return None


def has_keys(gw_id: int) -> bool:
    with session() as s:
        gw = s.get(Gateway, gw_id)
        return bool(gw and (gw.api_key or s.exec(select(ApiKey).where(ApiKey.gateway_id == gw_id)).first()))


# ---- API keys of the unified endpoint
def keys(settings: Optional[dict] = None) -> list[dict]:
    return list((settings or st.get_cached()).get("unified_keys") or [])


def match_key(token: str, settings: Optional[dict] = None) -> Optional[dict]:
    if not token:
        return None
    h = hash_key(token)
    return next((k for k in keys(settings) if secrets.compare_digest(k.get("key_hash", ""), h)), None)


def make_key(name: str) -> tuple[dict, str]:
    """-> (stored record without plaintext, plaintext). The plaintext is only ever returned once."""
    plain = "sgu-" + secrets.token_urlsafe(24)
    return {"id": secrets.token_hex(4), "name": name, "key_hash": hash_key(plain), "prefix": plain[:8] + "…", "created_at": time.time()}, plain


def access(token: str, settings: Optional[dict] = None) -> str:
    """How a caller may use the unified endpoint:
    "all"     a valid unified key: every exposed gateway, regardless of the gateways' own keys
    "gateway" no unified key needed (none configured) or the caller sent some other token: each gateway checks its own key
    "denied"  unified keys are configured and the caller sent nothing: anonymous access is closed, even for open gateways
    """
    if match_key(token, settings):
        return "all"
    if keys(settings) and not token:
        return "denied"
    return "gateway"
