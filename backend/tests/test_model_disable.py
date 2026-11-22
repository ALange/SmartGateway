import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.main import app
from app.models import Endpoint, Model


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def test_disabled_model_is_kept_but_not_served(client):
    with Session(db.engine) as s:
        ep = Endpoint(name="ep-dis", base_url="http://127.0.0.1:1/v1", status="online"); s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="m", capabilities=["chat"], max_slots=2, context_length=8000); s.add(m); s.commit(); s.refresh(m)
    slug = f"dis-{time.time_ns()}"
    mk = lambda name, enabled, aliases=(): {"public_name": name, "enabled": enabled, "aliases": list(aliases), "members": [{"model_id": m.id}]}  # noqa: E731
    g = client.post("/api/gateways", json={"name": "Dis", "slug": slug, "models": [mk("on", True), mk("off", False, ["old-name"])]}).json()
    assert {x["public_name"]: x["enabled"] for x in g["models"]} == {"on": True, "off": False}
    ids = [x["id"] for x in client.get(f"/gw/{slug}/v1/models").json()["data"]]
    assert ids == ["on"]                                                      # hidden from /models, aliases too
    for name in ("off", "old-name"):
        r = client.post(f"/gw/{slug}/v1/chat/completions", json={"model": name, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404 and "disabled" in r.json()["error"]["message"]
    r = client.post(f"/gw/{slug}/v1/chat/completions", json={"model": "nope", "messages": []})
    assert r.status_code == 404 and "disabled" not in r.json()["error"]["message"]
    # turned back on: configuration (members, aliases) survived
    back = client.put(f"/api/gateways/{g['id']}", json={"name": "Dis", "slug": slug, "models": [mk("on", True), mk("off", True, ["old-name"])]}).json()
    assert all(x["enabled"] for x in back["models"]) and len(back["models"][1]["members"]) == 1
    assert sorted(x["id"] for x in client.get(f"/gw/{slug}/v1/models").json()["data"]) == ["off", "old-name", "on"]
    # every model disabled
    client.put(f"/api/gateways/{g['id']}", json={"name": "Dis", "slug": slug, "models": [mk("on", False)]})
    r = client.post(f"/gw/{slug}/v1/chat/completions", json={"model": "on", "messages": []})
    assert r.status_code == 404 and "disabled" in r.json()["error"]["message"]
