import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db, settings_store as st, unified
from app.main import app
from app.models import Endpoint, Gateway, Model


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def gw(i, name, slug):
    return Gateway(id=i, name=name, slug=slug)


# ---- pure alias building
def test_aliases_use_gateway_names_and_default_model():
    gws = {1: gw(1, "Fast Chat", "fast"), 2: gw(2, "Brain", "brain")}
    models = {1: ["chat"], 2: ["thinker", "vision"]}
    a = unified.build_aliases([{"gateway_id": 1, "model": None}, {"gateway_id": 2, "model": "vision"}], gws, models)
    assert [(x["alias"], x["model"]) for x in a] == [
        ("Fast Chat", "chat"), ("Brain", "vision"), ("Brain/thinker", "thinker"), ("Brain/vision", "vision")]
    assert [x["default"] for x in a] == [True, True, False, False]


def test_alias_defaults_and_stale_entries():
    gws = {1: gw(1, "A", "a")}
    a = unified.build_aliases([{"gateway_id": 1, "model": "gone"}, {"gateway_id": 9, "model": None}, {"gateway_id": 1, "model": None}], gws, {1: ["m1", "m2"]})
    assert a[0]["model"] == "m1"                       # unknown default falls back to the first model
    assert not any(x["gateway"].id == 9 for x in a)    # deleted gateway skipped
    assert unified.build_aliases([{"gateway_id": 1}], gws, {}) == []   # no models -> nothing to serve


def test_duplicate_gateway_names_stay_unique():
    gws = {1: gw(1, "Same", "one"), 2: gw(2, "same", "two"), 3: gw(3, "Same", "three")}
    models = {i: ["m"] for i in gws}
    names = [x["alias"] for x in unified.build_aliases([{"gateway_id": i} for i in (1, 2, 3)], gws, models)]
    assert names[0] == "Same" and names[1] == "same (two)" and names[2] == "Same (three)"
    assert len({n.lower() for n in names}) == 3


def test_resolve_exact_ci_slug():
    gws = {1: gw(1, "Fast Chat", "fast")}
    al = unified.build_aliases([{"gateway_id": 1}], gws, {1: ["a", "b"]})
    assert unified.resolve("Fast Chat", al)["model"] == "a"
    assert unified.resolve("fast chat", al)["alias"] == "Fast Chat"
    assert unified.resolve("fast", al)["alias"] == "Fast Chat"                    # slug works too
    assert unified.resolve("Fast Chat/b", al)["model"] == "b"
    assert unified.resolve("nope", al) is None and unified.resolve("", al) is None


# ---- API + endpoint
def make_gateway(client, name, models=("mm",), keys=False):
    with Session(db.engine) as s:
        ep = Endpoint(name=f"ep-{name}", base_url="http://127.0.0.1:1/v1", status="online"); s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="m", capabilities=["chat", "vision"], max_slots=2, context_length=8000); s.add(m); s.commit(); s.refresh(m)
    body = {"name": name, "slug": f"{name.lower().replace(' ', '-')}-{time.time_ns()}", "models": [{"public_name": p, "members": [{"model_id": m.id}]} for p in models]}
    g = client.post("/api/gateways", json=body).json()
    token = None
    if keys:
        token = client.post(f"/api/gateways/{g['id']}/keys", json={"name": "k"}).json()["key"]
    return g, token


def test_disabled_by_default_and_api_validation(client):
    assert client.get("/unified/v1/models").status_code == 404
    v = client.get("/api/unified").json()
    assert v["enabled"] is False and v["path"] == "/unified/v1" and v["model_names"] == []
    g, _ = make_gateway(client, "Val GW", ("a", "b"))
    assert client.put("/api/unified", json={"gateways": [{"id": 99999}]}).status_code == 400
    assert client.put("/api/unified", json={"gateways": [{"id": g["id"]}, {"id": g["id"]}]}).status_code == 400
    assert client.put("/api/unified", json={"gateways": [{"id": g["id"], "model": "zzz"}]}).status_code == 400
    ok = client.put("/api/unified", json={"enabled": True, "gateways": [{"id": g["id"], "model": "b"}]}).json()
    assert ok["enabled"] and ok["model_names"] == ["Val GW", "Val GW/a", "Val GW/b"]
    row = next(x for x in ok["gateways"] if x["id"] == g["id"])
    assert row["exposed"] and row["model"] == "b" and row["models"] == ["a", "b"]
    client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_models_list_respects_gateway_auth(client):
    open_gw, _ = make_gateway(client, "Open One")
    locked, token = make_gateway(client, "Locked One", keys=True)
    other, other_token = make_gateway(client, "Other Locked", keys=True)
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": open_gw["id"]}, {"id": locked["id"]}, {"id": other["id"]}]})
    names = lambda r: [m["id"] for m in r.json()["data"]]  # noqa: E731
    assert names(client.get("/unified/v1/models")) == ["Open One"]                                   # anonymous: open gateways only
    assert names(client.get("/unified/v1/models", headers={"Authorization": f"Bearer {token}"})) == ["Open One", "Locked One"]
    assert names(client.get("/unified/v1/models", headers={"x-api-key": other_token})) == ["Open One", "Other Locked"]
    m = client.get("/unified/v1/models").json()["data"][0]
    assert m["object"] == "model" and "chat" in m["capabilities"] and m["owned_by"].startswith("smartgateway/")

    r = client.post("/unified/v1/chat/completions", json={"model": "Locked One", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401                                                                       # gateway's own key is required
    r = client.post("/unified/v1/chat/completions", headers={"Authorization": f"Bearer {other_token}"}, json={"model": "Locked One", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401                                                                       # another gateway's key is no good
    r = client.post("/unified/v1/chat/completions", json={"model": "Nope", "messages": []})
    assert r.status_code == 404 and r.json()["error"]["type"] == "model_not_found"
    assert "Open One" in r.json()["error"]["message"] and "Locked One" not in r.json()["error"]["message"]   # no leaking of protected names
    client.put("/api/unified", json={"enabled": False})
    assert client.get("/unified/v1/models").status_code == 404


def test_unexposed_gateway_is_not_reachable(client):
    a, _ = make_gateway(client, "Exposed X")
    b, _ = make_gateway(client, "Hidden Y")
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": a["id"]}]})
    r = client.post("/unified/v1/chat/completions", json={"model": "Hidden Y", "messages": []})
    assert r.status_code == 404
    r = client.post("/unified/v1/chat/completions", json={"model": b["slug"], "messages": []})        # nor by slug
    assert r.status_code == 404
    client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_unified_api_keys(client):
    open_gw, _ = make_gateway(client, "KeyOpen")
    locked, gkey = make_gateway(client, "KeyLocked", keys=True)
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": open_gw["id"]}, {"id": locked["id"]}]})
    ids = lambda r: [m["id"] for m in r.json()["data"]]  # noqa: E731
    assert ids(client.get("/unified/v1/models")) == ["KeyOpen"]                                   # no unified keys yet: as before
    made = client.post("/api/unified/keys", json={"name": "ci"}).json()
    token = made["key"]
    assert token.startswith("sgu-") and made["prefix"] == token[:8] + "…" and "key_hash" not in made
    assert client.post("/api/unified/keys", json={"name": "  "}).status_code == 400
    listed = client.get("/api/unified").json()["keys"]
    assert [k["name"] for k in listed] == ["ci"] and "key" not in listed[0] and "key_hash" not in listed[0]
    assert token not in client.get("/api/settings").text                                           # plaintext is never stored/returned again

    assert client.get("/unified/v1/models").status_code == 401                                     # anonymous is closed, even for open gateways
    assert client.get("/unified/v1/models", headers={"Authorization": "Bearer junk"}).json()["data"] == []  # a junk token unlocks nothing
    r = client.post("/unified/v1/chat/completions", headers={"Authorization": "Bearer junk"}, json={"model": "KeyOpen", "messages": []})
    assert r.status_code == 401
    assert ids(client.get("/unified/v1/models", headers={"Authorization": f"Bearer {token}"})) == ["KeyOpen", "KeyLocked"]   # unified key: everything
    assert ids(client.get("/unified/v1/models", headers={"x-api-key": token})) == ["KeyOpen", "KeyLocked"]
    assert ids(client.get("/unified/v1/models", headers={"Authorization": f"Bearer {gkey}"})) == ["KeyLocked"]              # a gateway key still opens its own gateway
    assert client.post("/unified/v1/messages", json={"model": "KeyOpen", "max_tokens": 5, "messages": []}).json()["type"] == "error"  # Anthropic-shaped 401

    assert client.delete(f"/api/unified/keys/{made['id']}").status_code == 200
    assert client.delete(f"/api/unified/keys/{made['id']}").status_code == 404
    assert ids(client.get("/unified/v1/models")) == ["KeyOpen"]                                    # last key removed -> open again
    client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_unified_aliases(client):
    a = unified.build_aliases([{"gateway_id": 1, "aliases": ["gpt-4o", "fast"]}], {1: gw(1, "Fast Chat", "fast")}, {1: ["chat"]})
    assert [(x["alias"], x["model"]) for x in a] == [("Fast Chat", "chat"), ("gpt-4o", "chat"), ("fast", "chat")]
    g, _ = make_gateway(client, "Alias GW", ("a", "b"))
    other, _ = make_gateway(client, "Alias Other")
    ok = client.put("/api/unified", json={"enabled": True, "gateways": [{"id": g["id"], "model": "b", "aliases": [" gpt-4o ", "GPT-4O", ""]}]}).json()
    assert ok["model_names"] == ["Alias GW", "gpt-4o", "Alias GW/a", "Alias GW/b"]
    assert next(x for x in ok["gateways"] if x["id"] == g["id"])["aliases"] == ["gpt-4o"]
    m = client.get("/unified/v1/models").json()["data"]
    assert "gpt-4o" in [x["id"] for x in m]
    # clashes are rejected, not silently renamed
    bad = client.put("/api/unified", json={"gateways": [{"id": g["id"], "aliases": ["Alias Other"]}, {"id": other["id"]}]})
    assert bad.status_code == 400 and "clashes" in bad.json()["detail"]
    bad = client.put("/api/unified", json={"gateways": [{"id": g["id"], "aliases": ["same"]}, {"id": other["id"], "aliases": ["SAME"]}]})
    assert bad.status_code == 400
    client.put("/api/unified", json={"enabled": False, "gateways": []})
