import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db, stress
from app.main import app
from app.models import Endpoint, Model, StressRun


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def make_gateway(client, models=None):
    with Session(db.engine) as s:
        ep = Endpoint(name="stress-ep", base_url="http://127.0.0.1:1/v1", status="online")
        s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="m", capabilities=["chat"], max_slots=3, context_length=4000)
        s.add(m); s.commit(); s.refresh(m)
    body = {"name": "g", "slug": f"stress-{time.time_ns()}", "models": models or [{"public_name": "mm", "members": [{"model_id": m.id}]}]}
    return client.post("/api/gateways", json=body).json()


def test_start_requires_acknowledgement_and_valid_input(client):
    g = make_gateway(client)
    url = f"/api/gateways/{g['id']}/stress"
    assert client.post(url, json={}).status_code == 400                                              # not acknowledged
    assert "acknowledge" in client.post(url, json={}).json()["detail"]
    assert client.post(url, json={"acknowledge": True, "levels": [0]}).status_code == 400            # bad level
    assert client.post(url, json={"acknowledge": True, "model": "nope"}).status_code == 404
    assert client.post("/api/gateways/987654/stress", json={"acknowledge": True}).status_code == 404
    big = client.post(url, json={"acknowledge": True, "prompt_tokens": 20000, "max_tokens": 128, "levels": [1]})
    assert big.status_code == 400 and "context window" in big.json()["detail"]                        # members only have 4000 tokens
    empty = client.post("/api/gateways", json={"name": "empty", "slug": f"empty-{time.time_ns()}", "models": []}).json()
    assert client.post(f"/api/gateways/{empty['id']}/stress", json={"acknowledge": True}).status_code == 400
    client.delete(f"/api/gateways/{g['id']}")


def test_unknown_job_and_presets(client):
    assert client.get("/api/stress/doesnotexist").status_code == 404
    assert client.post("/api/stress/doesnotexist/stop").status_code == 404
    p = client.get("/api/stress/presets").json()
    assert p["quick"]["levels"] == [1, 2, 4, 8] and p["thorough"]["levels"][-1] == 64
    assert client.get("/api/stress/current").json() is None


def test_run_history_endpoints(client):
    g = make_gateway(client)
    with Session(db.engine) as s:
        r = StressRun(gateway_id=g["id"], public_model="mm", status="done", config={"levels": [1]}, finished_at=time.time(),
                      result={"summary": {"peak": {"concurrency": 1, "agg_tps": 12.0}}, "stages": []})
        s.add(r); s.commit(); s.refresh(r)
    rows = client.get(f"/api/gateways/{g['id']}/stress/runs").json()
    assert len(rows) == 1 and rows[0]["summary"]["peak"]["agg_tps"] == 12.0 and rows[0]["status"] == "done"
    assert client.get(f"/api/stress/runs/{rows[0]['id']}").json()["config"] == {"levels": [1]}
    assert client.delete(f"/api/stress/runs/{rows[0]['id']}").status_code == 200
    assert client.get(f"/api/gateways/{g['id']}/stress/runs").json() == []
    assert client.get("/api/stress/runs/999999").status_code == 404


def test_only_one_stress_test_at_a_time(client, monkeypatch):
    g = make_gateway(client)

    class Fake:
        id, status = "fake", "running"
        gateway = type("G", (), {"name": "other"})()

    monkeypatch.setattr(stress, "CURRENT", Fake())
    r = client.post(f"/api/gateways/{g['id']}/stress", json={"acknowledge": True, "levels": [1]})
    assert r.status_code == 409 and r.json()["detail"]["job_id"] == "fake"
