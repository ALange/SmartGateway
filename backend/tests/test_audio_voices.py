"""GET /v1/audio/voices: forwards the upstream's voice list, falls back to an admin-set list, merges TTS models."""
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db, proxy, settings_store as st
from app.main import app
from app.models import Endpoint, Model
from app.router import health


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


class Up:
    def __init__(self):
        self.replies: dict = {}   # host -> (status, json) for /audio/voices
        self.calls: list = []     # (host, authorization)


@pytest.fixture
def up(monkeypatch):
    u = Up()
    proxy._voices_cache.clear()

    def handler(request: httpx.Request):
        if request.url.path.endswith("/audio/voices"):
            u.calls.append((request.url.host, request.headers.get("authorization")))
            status, data = u.replies.get(request.url.host, (404, {"detail": "Not Found"}))
            return httpx.Response(status, json=data)
        return httpx.Response(404, json={"detail": "nope"})

    monkeypatch.setattr(proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield u
    proxy._voices_cache.clear()


def make_gw(client, models, keys=None):
    """models: [(public_name, upstream host, capabilities, admin-set voices)] -> (gateway json, tokens)"""
    body_models = []
    for name, host, caps, voices, *rest in models:
        with Session(db.engine) as s:
            ep = Endpoint(name=f"ep-{host}-{time.time_ns()}", base_url=f"http://{host}/v1", api_key="sk-up" if host == "kokoro" else None, status="online")
            s.add(ep); s.commit(); s.refresh(ep)
            m = Model(endpoint_id=ep.id, upstream_id="u", capabilities=caps, max_slots=2, context_length=8000)
            s.add(m); s.commit(); s.refresh(m)
            ep_id, m_id = ep.id, m.id
        health.status[ep_id] = "online"
        body_models.append({"public_name": name, "voices": voices, "enabled": rest[0] if rest else True, "members": [{"model_id": m_id}]})
    g = client.post("/api/gateways", json={"name": f"v-{time.time_ns()}", "models": body_models})
    assert g.status_code == 200, g.text
    g = g.json()
    tokens = {}
    for name, allowed in (keys or {}).items():
        tokens[name] = client.post(f"/api/gateways/{g['id']}/keys", json={"name": name, "allowed_models": allowed}).json()["key"]
    return g, tokens


def voices(client, g, q="", **kw):
    return client.get(f"/api/admin-gw/{g['slug']}/v1/audio/voices{q}", **kw)


def test_upstream_voices_pass_through_with_upstream_auth(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart", "am_michael"]})
    g, _ = make_gw(client, [("speech", "kokoro", ["tts"], [])])
    r = voices(client, g)
    assert r.status_code == 200 and r.json() == {"voices": ["af_heart", "am_michael"]}
    assert up.calls == [("kokoro", "Bearer sk-up")]
    voices(client, g)                                    # served from the short cache
    assert len(up.calls) == 1


def test_bare_list_and_object_items(client, up):
    up.replies["srv1"] = (200, [{"id": "a", "name": "Alpha"}, {"voice_id": "b"}, {"nothing": 1}, ""])
    g, _ = make_gw(client, [("speech", "srv1", ["tts"], [])])
    assert voices(client, g).json() == {"voices": [{"id": "a", "name": "Alpha"}, {"voice_id": "b"}]}


def test_admin_set_voices_win_and_fill_in_when_upstream_has_none(client, up):
    g, _ = make_gw(client, [("speech", "novoices", ["tts"], [" nova ", "echo", "nova", ""])])
    assert voices(client, g).json() == {"voices": ["nova", "echo"]}        # cleaned, no upstream call needed
    assert up.calls == []
    g2, _ = make_gw(client, [("speech", "kokoro", ["tts"], ["custom"])])
    up.replies["kokoro"] = (200, {"voices": ["from-upstream"]})
    assert voices(client, g2).json() == {"voices": ["custom"]}             # the admin's list takes priority
    assert up.calls == []


def test_no_voices_anywhere_is_a_clear_404(client, up):
    g, _ = make_gw(client, [("speech", "novoices", ["tts"], [])])
    r = voices(client, g)
    assert r.status_code == 404 and r.json()["error"]["type"] == "voices_unavailable"
    assert "gateway editor" in r.json()["error"]["message"]


def test_models_are_merged_and_selectable(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart", "shared"]})
    up.replies["piper"] = (200, {"voices": ["shared", "lessac"]})
    g, _ = make_gw(client, [("kokoro-tts", "kokoro", ["tts"], []), ("piper-tts", "piper", ["tts"], []), ("chat", "kokoro", ["chat"], [])])
    assert voices(client, g).json() == {"voices": ["af_heart", "shared", "lessac"]}      # de-duplicated, chat model ignored
    assert voices(client, g, "?model=piper-tts").json() == {"voices": ["shared", "lessac"]}
    r = voices(client, g, "?model=chat")
    assert r.status_code == 400 and r.json()["error"]["type"] == "capability_unavailable"
    r = voices(client, g, "?model=zzz")
    assert r.status_code == 404 and r.json()["error"]["type"] == "model_not_found"


def test_disabled_model_is_not_listed(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart"]})
    up.replies["piper"] = (200, {"voices": ["lessac"]})
    g, _ = make_gw(client, [("on", "kokoro", ["tts"], []), ("off", "piper", ["tts"], [], False)])
    assert voices(client, g).json() == {"voices": ["af_heart"]}
    assert voices(client, g, "?model=off").status_code == 404


def test_gateway_keys_and_allowed_models(client, up, monkeypatch):
    monkeypatch.setattr(st, "get_cached", st.get_cached)
    up.replies["kokoro"] = (200, {"voices": ["af_heart"]})
    up.replies["piper"] = (200, {"voices": ["lessac"]})
    g, tok = make_gw(client, [("kokoro-tts", "kokoro", ["tts"], []), ("piper-tts", "piper", ["tts"], [])],
                     keys={"all": [], "piper-only": ["piper-tts"]})
    url = f"/gw/{g['slug']}/v1/audio/voices"
    assert client.get(url).status_code == 401                                              # keys are required like for every route
    assert client.get(url, headers={"Authorization": f"Bearer {tok['all']}"}).json() == {"voices": ["af_heart", "lessac"]}
    only = {"Authorization": f"Bearer {tok['piper-only']}"}
    assert client.get(url, headers=only).json() == {"voices": ["lessac"]}
    assert client.get(url + "?model=kokoro-tts", headers=only).status_code == 403
    # the default /v1 URL finds the gateway from the key
    assert client.get("/v1/audio/voices", headers=only).json() == {"voices": ["lessac"]}
    # x-api-key alone means an Anthropic-style client: errors keep that shape
    r = client.get(url, headers={"x-api-key": "wrong"})
    assert r.status_code == 401 and r.json().get("type") == "error"


def test_unified_voices_need_a_model(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart"]})
    up.replies["piper"] = (200, {"voices": ["lessac"]})
    g, _ = make_gw(client, [("speech", "kokoro", ["tts"], []), ("other", "piper", ["tts"], [])])
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": g["id"]}]})
    try:
        r = client.get("/unified/v1/audio/voices")
        assert r.status_code == 400
        assert client.get("/unified/v1/audio/voices?model=nope").status_code == 404
        r = client.get(f"/unified/v1/audio/voices?model={g['name']}")            # the gateway name = its default (first) model
        assert r.status_code == 200 and r.json() == {"voices": ["af_heart"]}
        r = client.get(f"/unified/v1/audio/voices?model={g['name']}/other")
        assert r.status_code == 200 and r.json() == {"voices": ["lessac"]}
    finally:
        client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_upstream_failure_does_not_leak_details(client, up):
    up.replies["kokoro"] = (500, {"detail": "internal path /srv/secret"})
    g, _ = make_gw(client, [("speech", "kokoro", ["tts"], [])])
    r = voices(client, g)
    assert r.status_code == 404 and "secret" not in r.text and "kokoro" not in r.text
    # a failed lookup is remembered briefly instead of hammering the upstream
    voices(client, g)
    assert len(up.calls) == 1
