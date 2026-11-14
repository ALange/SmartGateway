"""Admin audit trail: who changed what. Only field *names* of a request body are recorded, never values (keys, passwords)."""
import json
import re
from typing import Optional

from .db import session
from .models import AuditEvent

SKIP = re.compile(r"/(test|preview)$|^/api/(login|logout|ws|session)$|/stress/[^/]+/stop$")
_PATH = re.compile(r"^/api/(?P<type>[a-z-]+)(?:/(?P<id>[^/]+))?(?:/(?P<sub>[a-z-]+(?:/[a-z-]+)*))?$")


def classify(method: str, path: str) -> Optional[dict]:
    """Map a mutating admin API call to action / target. None = not worth auditing."""
    if method in ("GET", "HEAD", "OPTIONS") or not path.startswith("/api/") or SKIP.search(path):
        return None
    m = _PATH.match(path)
    if not m:
        return {"action": f"{method.lower()} {path}", "target_type": "", "target_id": ""}
    typ, ident, sub = m["type"], m["id"], m["sub"]
    if ident and not ident.isdigit() and not sub:  # /api/settings/compression, /api/content-filters/presets
        typ, ident, sub = typ, "", ident
    verb = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(method, method.lower())
    if sub:
        verb = sub.replace("/", " ")
        if method == "POST" and sub in ("keys", "stress"):
            return {"action": f"create {sub}", "target_type": typ, "target_id": ident or ""}
    return {"action": f"{verb} {typ}".strip(), "target_type": typ, "target_id": ident or ""}


def body_fields(raw: bytes) -> list[str]:
    try:
        d = json.loads(raw or b"null")
    except Exception:  # noqa: BLE001
        return []
    return sorted(d.keys())[:40] if isinstance(d, dict) else []


def record(actor: str, ip: str, action: str, target_type: str = "", target_id: str = "", status: int = 200,
           method: str = "", path: str = "", fields: Optional[list] = None, summary: str = "") -> None:
    try:
        with session() as s:
            s.add(AuditEvent(actor=actor, ip=ip, action=action, target_type=target_type, target_id=str(target_id),
                             status=status, method=method, path=path, fields=fields or None, summary=summary))
            s.commit()
    except Exception:  # noqa: BLE001  (auditing must never break an admin action)
        pass
