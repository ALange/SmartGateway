import asyncio
import base64
import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlmodel import select

from pydantic import BaseModel

from . import audit, config, crypto, proxy, retention
from .api import compression as compression_api, mcp_endpoints as mcp_endpoints_api, mcp_gateways as mcp_gateways_api, content_filters as content_filters_api, endpoints, gateways, insights as insights_api, maintenance as maintenance_api, settings as settings_api, stats, stress as stress_api, unified as unified_api
from .db import engine, init_db, session
from .mcp import registry as mcp_registry, server as mcp_server
from .models import Endpoint, McpEndpoint
from .router import health


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session() as s:
        for e in s.exec(select(Endpoint)).all():
            health.status[e.id] = e.status
        for m in s.exec(select(McpEndpoint)).all():
            mcp_registry.status[m.id] = m.status
    from . import council, settings_store
    council.sync_models()   # drops the hidden council gateway when no council uses endpoint models
    from .router.cache import cache
    from .router.scheduler import scheduler
    await cache.reconfigure(settings_store.get_all())
    tasks = [asyncio.create_task(health.loop()), asyncio.create_task(health.slot_sync_loop()),
             asyncio.create_task(scheduler.run_ticker()), asyncio.create_task(retention.loop()), asyncio.create_task(mcp_registry.loop())]
    yield
    for t in tasks:
        t.cancel()
    await cache.redis.close() if cache.redis else None
    await proxy.client.aclose()


app = FastAPI(title="SmartGateway", lifespan=lifespan)


COOKIE = "sg_session"
SESSION_TTL = 7 * 86400
_fails: dict[str, list] = {}


def _sign(exp: int) -> str:
    return hmac.new(crypto._load_secret(), f"admin:{exp}".encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    exp = int(time.time()) + SESSION_TTL
    return f"{exp}.{_sign(exp)}"


def valid_session(token: str | None) -> bool:
    try:
        exp, sig = (token or "").split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))
    except Exception:  # noqa: BLE001
        return False


def request_authed(headers, cookies) -> bool:
    if not config.ADMIN_PASSWORD:
        return True
    if valid_session(cookies.get(COOKIE)):
        return True
    h = headers.get("authorization", "")
    try:
        if h.lower().startswith("bearer "):
            return secrets.compare_digest(h[7:], config.ADMIN_PASSWORD)
        if h.lower().startswith("basic "):
            return secrets.compare_digest(base64.b64decode(h[6:]).decode().split(":", 1)[1], config.ADMIN_PASSWORD)
    except Exception:  # noqa: BLE001
        return False
    return False


CORS_ORIGINS = [o.strip() for o in os.environ.get("SG_CORS_ORIGINS", "").split(",") if o.strip()]


@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    is_gateway = p.startswith(("/gw/", "/v1/", "/unified/", "/mcp/"))
    # CORS for the OpenAI-compatible surface only
    cors = {}
    if is_gateway and CORS_ORIGINS:
        origin = request.headers.get("origin", "")
        if "*" in CORS_ORIGINS or origin in CORS_ORIGINS:
            cors = {"Access-Control-Allow-Origin": "*" if "*" in CORS_ORIGINS else origin, "Vary": "Origin",
                    "Access-Control-Allow-Headers": "authorization,content-type,x-api-key,anthropic-version,x-sg-require,x-sg-session,x-sg-cache,mcp-session-id,mcp-protocol-version,last-event-id,accept",
                    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS", "Access-Control-Expose-Headers": "X-SG-Upstream,X-SG-Endpoint,X-SG-Request-Id,X-SG-Cache,Mcp-Session-Id"}
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
    # admin protection for the management API (UI shell stays public so the login page can load)
    if config.ADMIN_PASSWORD and p.startswith("/api/") and p not in ("/api/login", "/api/session", "/api/ws"):
        if not request_authed(request.headers, request.cookies):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    audited = None if is_gateway else audit.classify(request.method, p)
    fields: list = []
    if audited and "json" in request.headers.get("content-type", ""):
        fields = audit.body_fields(await request.body())
    resp = await call_next(request)
    if audited and resp.status_code < 400:
        audit.record(admin_actor(), request.client.host if request.client else "", audited["action"], audited["target_type"],
                     audited["target_id"], resp.status_code, request.method, p, fields)
    for k, v in cors.items():
        resp.headers[k] = v
    return resp


def admin_actor() -> str:
    return os.environ.get("SG_ADMIN_USER", "admin") if config.ADMIN_PASSWORD else "open-access"


class LoginIn(BaseModel):
    username: str = "admin"
    password: str


@app.get("/api/session")
def session_info(request: Request):
    return {"auth_required": bool(config.ADMIN_PASSWORD), "authenticated": request_authed(request.headers, request.cookies)}


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    f = _fails.setdefault(ip, [0, time.time()])
    if time.time() - f[1] > 300:
        f[0], f[1] = 0, time.time()
    if f[0] >= 5:
        return JSONResponse({"detail": "Too many failed attempts, try again in a few minutes"}, status_code=429)
    ok = (not config.ADMIN_PASSWORD) or (
        secrets.compare_digest(body.password, config.ADMIN_PASSWORD)
        and secrets.compare_digest(body.username, os.environ.get("SG_ADMIN_USER", "admin")))
    if not ok:
        f[0] += 1
        audit.record(body.username[:60], ip, "login failed", "session", status=401, method="POST", path="/api/login")
        return JSONResponse({"detail": "Invalid credentials"}, status_code=401)
    f[0] = 0
    audit.record(body.username[:60], ip, "login", "session", method="POST", path="/api/login")
    r = JSONResponse({"ok": True})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(COOKIE, make_session(), max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure)
    return r


@app.post("/api/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE)
    return r


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(endpoints.router)
app.include_router(gateways.router)
app.include_router(stats.router)
app.include_router(maintenance_api.router)
app.include_router(settings_api.router)
app.include_router(compression_api.router)
app.include_router(content_filters_api.router)
app.include_router(insights_api.router)
app.include_router(stress_api.router)
app.include_router(unified_api.router)
app.include_router(mcp_endpoints_api.router)
app.include_router(mcp_gateways_api.router)
app.include_router(mcp_server.router)
app.include_router(proxy.router)

if os.path.isdir(config.STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(config.STATIC_DIR, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith(("api/", "gw/", "v1/", "unified/", "mcp/")):
            return JSONResponse({"error": "not found"}, status_code=404)
        # only files inside the static dir are served: resolve symlinks/".." and absolute paths first
        root = os.path.realpath(config.STATIC_DIR)
        f = os.path.realpath(os.path.join(root, full_path))
        if full_path and f.startswith(root + os.sep) and os.path.isfile(f):
            return FileResponse(f)
        # the HTML shell must always be revalidated so a new deploy is picked up (assets are content-hashed)
        return FileResponse(os.path.join(config.STATIC_DIR, "index.html"), headers={"Cache-Control": "no-cache"})
