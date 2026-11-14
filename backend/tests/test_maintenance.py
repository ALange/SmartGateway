import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import audit, db, retention
from app.db import session
from app.main import app
from app.models import AuditEvent, RequestLog


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def uniq(p):
    return f"{p}-{time.time_ns()}"


def add_logs(n, age_days, gw=987654):
    with session() as s:
        for _ in range(n):
            s.add(RequestLog(ts=time.time() - age_days * 86400, gateway_id=gw, public_model="m"))
        s.commit()


def count(gw):
    with session() as s:
        return len(s.exec(select(RequestLog).where(RequestLog.gateway_id == gw)).all())


def test_prune_only_old_rows_in_batches(client, monkeypatch):
    gw = 900001
    add_logs(7, 40, gw); add_logs(3, 1, gw)
    monkeypatch.setattr(retention, "BATCH", 2)
    res = retention.prune_once({"log_retention_days": 30})
    assert res["requestlog"] >= 7 and count(gw) == 3
    add_logs(2, 400, gw)
    retention.prune_once({"log_retention_days": 0})  # 0 keeps everything
    assert count(gw) == 5


def test_retention_setting_validation_and_prune_endpoint(client):
    assert client.put("/api/settings", json={"log_retention_days": -1}).status_code == 400
    r = client.put("/api/settings", json={"log_retention_days": 0, "audit_retention_days": 0})
    assert r.status_code == 200 and r.json()["log_retention_days"] == 0
    assert client.post("/api/maintenance/prune").status_code == 200
    info = client.get("/api/maintenance/db").json()
    assert info["size_bytes"] > 0 and "requestlog" in info["rows"]
    assert client.post("/api/maintenance/vacuum").status_code == 200


def test_export_csv_and_jsonl_filters(client):
    gw = 900002
    add_logs(3, 0, gw); add_logs(2, 0, gw + 1)
    r = client.get(f"/api/logs/export?format=csv&gateway_id={gw}")
    lines = r.text.strip().splitlines()
    assert r.headers["content-type"].startswith("text/csv") and len(lines) == 4 and lines[0].startswith("id,ts,gateway_id")
    r = client.get(f"/api/logs/export?format=jsonl&gateway_id={gw + 1}")
    rows = [json.loads(x) for x in r.text.splitlines()]
    assert len(rows) == 2 and all(x["gateway_id"] == gw + 1 for x in rows)


def last_audit(**kw):
    with session() as s:
        q = select(AuditEvent).order_by(AuditEvent.id.desc())
        for k, v in kw.items():
            q = q.where(getattr(AuditEvent, k) == v)
        return s.exec(q).first()


def test_audit_records_changes_without_values(client):
    name = uniq("secretname")
    r = client.post("/api/content-filters", json={"name": name, "pattern": "swordfish-xyz", "stage": "input", "action": "block"})
    assert r.status_code == 200
    fid = r.json()["id"]
    ev = last_audit(action="create content-filters")
    assert ev and ev.status == 200 and "pattern" in ev.fields
    client.put(f"/api/content-filters/{fid}", json={**r.json(), "pattern": "marlin-xyz"})
    assert last_audit(action="update content-filters", target_id=str(fid))
    client.delete(f"/api/content-filters/{fid}")
    assert last_audit(action="delete content-filters", target_id=str(fid))
    with session() as s:  # no values anywhere in the table
        blob = json.dumps([e.model_dump() for e in s.exec(select(AuditEvent)).all()])
    assert "swordfish-xyz" not in blob and "marlin-xyz" not in blob and name not in blob


def test_audit_skips_failures_reads_and_tests(client):
    n = lambda: len(client.get("/api/audit?limit=500").json())
    before = n()
    assert client.delete("/api/content-filters/99999999").status_code == 404
    client.get("/api/content-filters")
    client.post("/api/content-filters/test", json={"pattern": "a", "text": "a"})
    assert n() == before


def test_audit_secret_never_stored(client):
    gws = client.get("/api/gateways").json()
    r = client.post("/api/gateways", json={"name": uniq("gw"), "slug": uniq("gw")[:30].replace("_", "-")})
    if r.status_code != 200:
        pytest.skip(r.text)
    gid = r.json()["id"]
    k = client.post(f"/api/gateways/{gid}/keys", json={"name": "k"})
    plain = json.dumps(k.json())
    secret = next((v for v in k.json().values() if isinstance(v, str) and v.startswith("sk-")), None)
    with session() as s:
        blob = json.dumps([e.model_dump() for e in s.exec(select(AuditEvent)).all()])
    assert secret is None or secret not in blob
    assert last_audit(action="create keys")
    client.delete(f"/api/gateways/{gid}")


def test_audit_list_filters_and_export(client):
    r = client.get("/api/audit?action=content-filters&limit=5")
    assert r.status_code == 200 and all("content-filters" in e["action"] for e in r.json())
    assert client.get("/api/audit/export?format=jsonl").status_code == 200


def test_failed_login_is_audited(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "pw-for-test")
    user = uniq("intruder")
    assert client.post("/api/login", json={"username": user, "password": "nope"}).status_code in (401, 429)
    ev = last_audit(action="login failed")
    assert ev and ev.actor == user
    assert audit.classify("GET", "/api/gateways") is None and audit.classify("POST", "/api/content-filters/test") is None
