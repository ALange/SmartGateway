"""ChatGPT / Codex sign-in: the device-code flow the Codex CLI uses (no browser redirect, so it works inside Docker), token refresh and
the account identity found in the tokens. Tokens are only ever stored encrypted (models.OAuthAccount) and are never logged."""
import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

import httpx
from sqlmodel import select

from ..db import session
from ..models import Endpoint, OAuthAccount
from . import AccountError

log = logging.getLogger("smartgateway.accounts")

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"          # the public client id of the official Codex CLI
AUTH_BASE = os.environ.get("SG_CODEX_AUTH_BASE", "https://auth.openai.com").rstrip("/")
CODEX_BASE = os.environ.get("SG_CODEX_BASE", "https://chatgpt.com/backend-api/codex").rstrip("/")
REFRESH_MARGIN = 300                                  # refresh when the access token expires within 5 minutes
AUTH_CLAIM = "https://api.openai.com/auth"
PROFILE_CLAIM = "https://api.openai.com/profile"
DEAD_REFRESH = {"invalid_grant", "refresh_token_reused", "refresh_token_expired", "refresh_token_invalidated", "invalid_request"}


def _client() -> httpx.AsyncClient:
    from .. import proxy   # the shared client is also the test seam
    return proxy.client


def jwt_claims(token: Optional[str]) -> dict:
    """Payload of a JWT, unverified: the values are only shown to the admin or echoed back to their issuer, never trusted for access."""
    try:
        part = (token or "").split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return {}


def identity(access_token: Optional[str], id_token: Optional[str] = None) -> dict:
    a, i = jwt_claims(access_token), jwt_claims(id_token)
    auth = a.get(AUTH_CLAIM) or i.get(AUTH_CLAIM) or {}
    email = i.get("email") or (a.get(PROFILE_CLAIM) or {}).get("email") or a.get("email")
    return {"account_id": auth.get("chatgpt_account_id") or None, "plan": auth.get("chatgpt_plan_type") or None, "email": email or None}


# ------------------------------------------------------------------ device-code sign-in
async def device_start() -> dict:
    r = await _client().post(f"{AUTH_BASE}/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID}, timeout=20)
    if r.status_code != 200:
        raise AccountError(f"OpenAI did not start the sign-in (HTTP {r.status_code})")
    d = r.json()
    try:
        interval = max(2, int(d.get("interval") or 5))
    except (TypeError, ValueError):
        interval = 5
    return {"device_auth_id": d["device_auth_id"], "user_code": d.get("user_code") or d.get("usercode") or "",
            "verification_url": f"{AUTH_BASE}/codex/device", "interval": interval}


async def device_poll(device_auth_id: str, user_code: str) -> Optional[dict]:
    """None while the user has not approved yet; the token set once they have."""
    r = await _client().post(f"{AUTH_BASE}/api/accounts/deviceauth/token", json={"device_auth_id": device_auth_id, "user_code": user_code}, timeout=20)
    if r.status_code in (403, 404):
        return None
    if r.status_code != 200:
        raise AccountError(f"OpenAI refused the sign-in (HTTP {r.status_code})")
    d = r.json()
    return await _token_request({"grant_type": "authorization_code", "code": d["authorization_code"], "redirect_uri": f"{AUTH_BASE}/deviceauth/callback",
                                 "client_id": CLIENT_ID, "code_verifier": d["code_verifier"]})


async def _token_request(form: dict) -> dict:
    r = await _client().post(f"{AUTH_BASE}/oauth/token", data=form, timeout=20)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or not data.get("access_token"):
        err = data.get("error")
        err = err if isinstance(err, str) else (err or {}).get("code") or ""
        raise AccountError(f"token request refused (HTTP {r.status_code}{', ' + err if err else ''})", needs_login=err in DEAD_REFRESH or r.status_code in (400, 401))
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"), "id_token": data.get("id_token"),
            "expires_in": int(data.get("expires_in") or 3600)}


# ------------------------------------------------------------------ stored account + refresh
def save_tokens(endpoint_id: int, tokens: dict, kind: str = "codex") -> OAuthAccount:
    """Create or update the account row of an endpoint from a token response (a refresh keeps the old refresh token if none is returned)."""
    ident = identity(tokens["access_token"], tokens.get("id_token"))
    with session() as s:
        acc = s.get(OAuthAccount, endpoint_id) or OAuthAccount(endpoint_id=endpoint_id, kind=kind)
        acc.access_token = tokens["access_token"]
        acc.refresh_token = tokens.get("refresh_token") or acc.refresh_token
        acc.expires_at = time.time() + int(tokens.get("expires_in") or 3600)
        acc.account_id = ident["account_id"] or acc.account_id
        acc.email = ident["email"] or acc.email
        acc.plan = ident["plan"] or acc.plan
        acc.needs_login, acc.last_error, acc.last_refresh = False, None, time.time()
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return acc


def _mark(endpoint_id: int, needs_login: bool, error: str) -> None:
    with session() as s:
        acc = s.get(OAuthAccount, endpoint_id)
        if acc:
            acc.needs_login = acc.needs_login or needs_login
            acc.last_error = error[:200]
            s.add(acc)
            s.commit()


_locks: dict[int, asyncio.Lock] = {}


async def fresh_access(endpoint_id: int, force: bool = False) -> tuple[str, Optional[str]]:
    """(access token, ChatGPT account id), refreshing first when the token is (nearly) expired. One refresh at a time per account:
    refresh tokens rotate, so two concurrent refreshes would kill each other."""
    lock = _locks.setdefault(endpoint_id, asyncio.Lock())
    async with lock:
        with session() as s:
            acc = s.get(OAuthAccount, endpoint_id)
            if acc is None:
                raise AccountError("This endpoint has no signed-in account")
            if acc.needs_login or not acc.refresh_token:
                raise AccountError("The account must sign in again", needs_login=True)
            token, refresh, exp, account_id = acc.access_token, acc.refresh_token, acc.expires_at, acc.account_id
        if token and not force and exp - time.time() > REFRESH_MARGIN:
            return token, account_id
        try:
            tokens = await _token_request({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLIENT_ID, "scope": "openid profile email"})
        except AccountError as e:
            _mark(endpoint_id, e.needs_login, str(e))
            raise AccountError(f"Refreshing the account failed: {e}", needs_login=e.needs_login) from None
        except Exception as e:  # noqa: BLE001 - network trouble is not a reason to sign the account out
            _mark(endpoint_id, False, f"{type(e).__name__}")
            raise AccountError(f"Refreshing the account failed: {type(e).__name__}") from None
        acc = save_tokens(endpoint_id, tokens)
        return acc.access_token, acc.account_id


def account_info(endpoint_id: int) -> Optional[dict]:
    """What the admin may see about an account (never the tokens)."""
    with session() as s:
        acc = s.get(OAuthAccount, endpoint_id)
        if acc is None:
            return None
        return {"kind": acc.kind, "email": acc.email, "plan": acc.plan, "account_id": acc.account_id, "needs_login": acc.needs_login,
                "expires_at": acc.expires_at, "last_refresh": acc.last_refresh, "last_error": acc.last_error}


def find_endpoint_for(account_id: Optional[str], email: Optional[str]) -> Optional[int]:
    """The endpoint that already holds this ChatGPT account (a second sign-in of the same account updates it instead of adding a copy)."""
    if not account_id and not email:
        return None
    with session() as s:
        for acc in s.exec(select(OAuthAccount)).all():
            if (account_id and acc.account_id == account_id and (not email or not acc.email or acc.email == email)) or (not account_id and acc.email == email):
                if s.get(Endpoint, acc.endpoint_id):
                    return acc.endpoint_id
    return None
