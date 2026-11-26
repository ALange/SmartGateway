"""Sign in with a ChatGPT / Codex subscription account (device-code flow) and manage the signed-in accounts."""
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..accounts import AccountError, LABELS, codex_auth
from ..api import endpoints as endpoints_api
from ..db import get_session
from ..models import Endpoint, Model, OAuthAccount
from ..router import health

router = APIRouter(prefix="/api/accounts")
PROVIDER = "codex-oauth"
PENDING_TTL = 15 * 60
MAX_PENDING = 20
_pending: dict[str, dict] = {}   # session_id -> sign-in in progress (in memory: a restart just means "start again")


class StartIn(BaseModel):
    name: Optional[str] = None
    endpoint_id: Optional[int] = None   # sign in again for this account instead of adding a new one


class PollIn(BaseModel):
    session_id: str


def _prune() -> None:
    now = time.time()
    for k in [k for k, v in _pending.items() if v["expires"] < now]:
        _pending.pop(k, None)


@router.post("/codex/start")
async def start(body: StartIn, s: Session = Depends(get_session)):
    _prune()
    if len(_pending) >= MAX_PENDING:
        raise HTTPException(429, "Too many sign-ins in progress; finish or wait for them to expire")
    if body.endpoint_id is not None:
        ep = s.get(Endpoint, body.endpoint_id)
        if not ep or ep.provider != PROVIDER:
            raise HTTPException(404, "No such account")
    try:
        d = await codex_auth.device_start()
    except AccountError as e:
        raise HTTPException(502, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Cannot reach OpenAI: {type(e).__name__}")
    sid = secrets.token_urlsafe(16)
    _pending[sid] = {**d, "name": (body.name or "").strip()[:80], "endpoint_id": body.endpoint_id, "expires": time.time() + PENDING_TTL, "busy": False}
    return {"session_id": sid, "user_code": d["user_code"], "verification_url": d["verification_url"], "interval": d["interval"], "expires_in": PENDING_TTL}


@router.post("/codex/poll")
async def poll(body: PollIn, s: Session = Depends(get_session)):
    _prune()
    p = _pending.get(body.session_id)
    if not p:
        raise HTTPException(404, "This sign-in expired; start again")
    if p["busy"]:
        return {"status": "pending"}
    p["busy"] = True
    try:
        tokens = await codex_auth.device_poll(p["device_auth_id"], p["user_code"])
    except AccountError as e:
        _pending.pop(body.session_id, None)
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 - a network blip: keep waiting
        p["busy"] = False
        return {"status": "pending", "note": type(e).__name__}
    p["busy"] = False
    if tokens is None:
        return {"status": "pending"}
    _pending.pop(body.session_id, None)
    ident = codex_auth.identity(tokens["access_token"], tokens.get("id_token"))
    ep_id = p["endpoint_id"] or codex_auth.find_endpoint_for(ident["account_id"], ident["email"])
    if ep_id:   # the same account signing in again: replace its tokens, keep its endpoint, models and gateways
        ep = s.get(Endpoint, ep_id)
        codex_auth.save_tokens(ep.id, tokens)
        created = False
    else:
        label = ident["email"] or ident["plan"] or "account"
        ep = Endpoint(name=p["name"] or f"{LABELS[PROVIDER]} ({label})", base_url=codex_auth.CODEX_BASE, api_key=None, provider=PROVIDER, status="online")
        s.add(ep)
        s.commit()
        s.refresh(ep)
        codex_auth.save_tokens(ep.id, tokens)
        created = True
    try:
        await endpoints_api.sync_models(ep, s, probe=False)
        if created:   # the admin added this account on purpose: its models are usable at once
            for m in s.exec(select(Model).where(Model.endpoint_id == ep.id)).all():
                m.enabled = True
                s.add(m)
            s.commit()
    except Exception as e:  # noqa: BLE001
        if created:
            for m in s.exec(select(Model).where(Model.endpoint_id == ep.id)).all():
                endpoints_api._delete_model(s, m)
            acc = s.get(OAuthAccount, ep.id)
            if acc:
                s.delete(acc)
            s.delete(ep)
            s.commit()
            raise HTTPException(400, f"Signed in, but the model list could not be read: {e}")
    health.status[ep.id] = "online"
    ep.status, ep.last_error = "online", None
    s.add(ep)
    s.commit()
    s.refresh(ep)
    return {"status": "done", "created": created, "endpoint": endpoints_api.ep_out(ep, s)}


@router.get("")
def list_accounts(s: Session = Depends(get_session)):
    out = []
    for ep in s.exec(select(Endpoint).where(Endpoint.provider == PROVIDER)).all():
        out.append({"endpoint_id": ep.id, "name": ep.name, "status": ep.status, **(codex_auth.account_info(ep.id) or {})})
    return out
