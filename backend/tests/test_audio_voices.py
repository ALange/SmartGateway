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
        self.queries: list = []   # ?model= values seen
        self.need_model: set = set()  # hosts that answer 400 without ?model= (oMLX)


@pytest.fixture
def up(monkeypatch):
    u = Up()
    proxy._voices_cache.clear()

    def handler(request: httpx.Request):
        if request.url.path.endswith("/audio/voices"):
            u.calls.append((request.url.host, request.headers.get("authorization")))
            u.queries.append(request.url.params.get("model"))
            if request.url.host in u.need_model and not request.url.params.get("model"):
                return httpx.Response(400, json={"error": {"message": "'model' query parameter is required"}})
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


def test_unified_voices_with_and_without_model(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart"]})
    up.replies["piper"] = (200, {"voices": ["lessac"]})
    g, _ = make_gw(client, [("speech", "kokoro", ["tts"], []), ("other", "piper", ["tts"], [])])
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": g["id"]}]})
    try:
        assert client.get("/unified/v1/audio/voices?model=nope").status_code == 404
        r = client.get(f"/unified/v1/audio/voices?model={g['name']}")            # the gateway name = its default (first) model
        assert r.status_code == 200 and r.json() == {"voices": ["af_heart"]}
        r = client.get(f"/unified/v1/audio/voices?model={g['name']}/other")
        assert r.status_code == 200 and r.json() == {"voices": ["lessac"]}
        r = client.get("/unified/v1/audio/voices")                               # no model (Open WebUI): everything exposed, merged
        assert r.status_code == 200 and r.json() == {"voices": ["af_heart", "lessac"]}
    finally:
        client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_unified_voices_without_model_follow_access_rules(client, up):
    up.replies["kokoro"] = (200, {"voices": ["open-voice"]})
    up.replies["piper"] = (200, {"voices": ["locked-voice"]})
    open_gw, _ = make_gw(client, [("speech", "kokoro", ["tts"], [])])
    locked, tok = make_gw(client, [("speech", "piper", ["tts"], [])], keys={"k": []})
    client.put("/api/unified", json={"enabled": True, "gateways": [{"id": open_gw["id"]}, {"id": locked["id"]}]})
    try:
        assert client.get("/unified/v1/audio/voices").json() == {"voices": ["open-voice"]}            # anonymous: open gateways only
        r = client.get("/unified/v1/audio/voices", headers={"Authorization": f"Bearer {tok['k']}"})
        assert r.json() == {"voices": ["open-voice", "locked-voice"]}                                  # a gateway key adds its own gateway
        assert client.get(f"/unified/v1/audio/voices?model={locked['name']}").status_code == 401       # named, but not allowed
        ukey = client.post("/api/unified/keys", json={"name": "u"})
        assert ukey.status_code == 200, ukey.text
        try:                                                                                           # a unified key opens everything
            h = {"Authorization": f"Bearer {ukey.json()['key']}"}
            assert client.get("/unified/v1/audio/voices", headers=h).json() == {"voices": ["open-voice", "locked-voice"]}
            assert client.get("/unified/v1/audio/voices").status_code == 401                          # keys configured: anonymous is closed
            assert client.get(f"/unified/v1/audio/voices?model={locked['name']}", headers=h).json() == {"voices": ["locked-voice"]}
        finally:
            client.delete(f"/api/unified/keys/{ukey.json()['id']}")
    finally:
        client.put("/api/unified", json={"enabled": False, "gateways": []})


def test_unified_directly_exposed_tts_model(client, up):
    up.replies["kokoro"] = (200, {"voices": ["af_heart", "am_michael"]})
    with Session(db.engine) as s:
        ep = Endpoint(name=f"ep-direct-{time.time_ns()}", base_url="http://kokoro/v1", api_key="sk-up", status="online"); s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="kokoro", capabilities=["tts"], max_slots=2, context_length=0, enabled=True); s.add(m); s.commit(); s.refresh(m)
        ep_id, m_id = ep.id, m.id
    health.status[ep_id] = "online"
    client.put("/api/unified", json={"enabled": True, "gateways": [], "models": [{"model_id": m_id, "name": "kokoro-direct", "aliases": ["tts-1"]}]})
    try:
        for q in ("", "?model=kokoro-direct", "?model=tts-1"):
            r = client.get("/unified/v1/audio/voices" + q)
            assert r.status_code == 200 and r.json() == {"voices": ["af_heart", "am_michael"]}, q
        client.put("/api/unified", json={"enabled": True, "gateways": [], "models": [{"model_id": m_id, "name": "kokoro-direct", "disabled": True}]})
        assert client.get("/unified/v1/audio/voices").status_code == 404                                # a disabled direct model is not listed
        assert client.get("/unified/v1/audio/voices?model=kokoro-direct").status_code == 404
    finally:
        client.put("/api/unified", json={"enabled": False, "gateways": [], "models": []})


def test_upstream_failure_does_not_leak_details(client, up):
    up.replies["kokoro"] = (500, {"detail": "internal path /srv/secret"})
    g, _ = make_gw(client, [("speech", "kokoro", ["tts"], [])])
    r = voices(client, g)
    assert r.status_code == 404 and "secret" not in r.text and "kokoro" not in r.text
    # a failed lookup is remembered briefly instead of hammering the upstream
    voices(client, g)
    assert len(up.calls) == 1


# ---- discovery: voices are fetched when an endpoint is added or rescanned and offered as a checklist
import asyncio  # noqa: E402

from app import discovery  # noqa: E402


def fake_network(monkeypatch, models, voices):
    """voices: a list, or None for 'the upstream has no /audio/voices'. Returns a dict to change `voices` later and count calls."""
    st = {"voices": voices, "calls": 0, "models": []}

    async def ping(base, key):
        return True, None

    async def fetch(client, base, key):
        return [{"id": m} for m in models]

    async def slot(base, key):
        return "none"

    async def fv(base, key, provider=None, model=None):
        st["calls"] += 1
        st["models"].append(model)
        return st["voices"]
    monkeypatch.setattr(discovery, "ping", ping)
    monkeypatch.setattr(discovery, "fetch_models", fetch)
    monkeypatch.setattr(discovery, "detect_slot_source", slot)
    monkeypatch.setattr(discovery, "fetch_voices", fv)
    return st


def test_fetch_voices_parsing(monkeypatch):
    real = httpx.AsyncClient
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        return {"/ok/v1/audio/voices": httpx.Response(200, json={"voices": ["a", {"id": "b"}]}),
                "/bad/v1/audio/voices": httpx.Response(404, json={"detail": "nope"}),
                "/junk/v1/audio/voices": httpx.Response(200, text="<html>")}.get(request.url.path, httpx.Response(500))
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    run = lambda path, key=None: asyncio.run(discovery.fetch_voices(f"http://srv{path}/v1", key))  # noqa: E731
    assert run("/ok", "sk-1") == ["a", {"id": "b"}] and seen["auth"] == "Bearer sk-1"
    assert run("/bad") is None and run("/junk") is None


def test_endpoint_add_and_rescan_discover_voices(client, monkeypatch):
    st = fake_network(monkeypatch, ["kokoro", "chat-llm"], ["af_heart", "am_michael"])
    ep = client.post("/api/endpoints", json={"name": f"kok-{time.time_ns()}", "base_url": "http://kokoro-x/v1", "probe": False}).json()
    by = {m["upstream_id"]: m for m in ep["models"]}
    assert by["kokoro"]["capabilities"] == ["tts"] and by["kokoro"]["voices"] == ["af_heart", "am_michael"]
    assert not by["chat-llm"].get("voices")                                  # only TTS models carry voices
    assert st["calls"] == 1 and st["models"] == ["kokoro"]                   # asked for the TTS model only, with its id

    st["voices"] = ["af_heart", "af_bella", "am_michael"]                    # the upstream gained a voice
    ep = client.post(f"/api/endpoints/{ep['id']}/rescan?probe=false").json()
    assert {m["upstream_id"]: m for m in ep["models"]}["kokoro"]["voices"] == ["af_heart", "af_bella", "am_michael"]
    st["voices"] = None                                                       # a failed lookup keeps what was found before
    ep = client.post(f"/api/endpoints/{ep['id']}/rescan?probe=false").json()
    assert {m["upstream_id"]: m for m in ep["models"]}["kokoro"]["voices"] == ["af_heart", "af_bella", "am_michael"]


def test_refresh_voices_and_gateway_exposes_them_for_the_checklist(client, monkeypatch):
    st = fake_network(monkeypatch, ["kokoro", "chat-llm"], ["af_heart"])
    ep = client.post("/api/endpoints", json={"name": f"kok-{time.time_ns()}", "base_url": "http://kokoro-y/v1", "probe": False}).json()
    by = {m["upstream_id"]: m for m in ep["models"]}
    st["voices"] = ["af_heart", "am_adam"]
    r = client.post(f"/api/models/{by['kokoro']['id']}/voices/refresh")
    assert r.status_code == 200 and r.json()["voices"] == ["af_heart", "am_adam"]
    assert client.post(f"/api/models/{by['chat-llm']['id']}/voices/refresh").status_code == 400      # not a TTS model
    st["voices"] = None
    r = client.post(f"/api/models/{by['kokoro']['id']}/voices/refresh")
    assert r.status_code == 400 and "gateway editor" in r.json()["detail"]
    assert client.post("/api/models/999999/voices/refresh").status_code == 404
    # the gateway read model carries the discovered voices of each member, so the editor can show them as a checklist
    client.patch(f"/api/models/{by['kokoro']['id']}", json={"enabled": True})
    g = client.post("/api/gateways", json={"name": f"vg-{time.time_ns()}", "models": [{"public_name": "speech", "members": [{"model_id": by["kokoro"]["id"]}]}]}).json()
    assert g["models"][0]["members"][0]["voices"] == ["af_heart", "am_adam"]


# ---- oMLX-style servers list voices per model and refuse /audio/voices without ?model=
def test_live_route_sends_the_model_id_to_servers_that_require_it(client, up):
    up.need_model.add("omlx")
    up.replies["omlx"] = (200, {"model": "u", "voices": ["af_heart", "am_adam"]})
    g, _ = make_gw(client, [("speech", "omlx", ["tts"], [])])
    r = voices(client, g)
    assert r.status_code == 200 and r.json() == {"voices": ["af_heart", "am_adam"]}
    assert up.queries == ["u"]                                                 # the upstream model id, not the public name


def test_discovery_sends_the_model_id(monkeypatch):
    real = httpx.AsyncClient
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.params.get("model"))
        if not request.url.params.get("model"):
            return httpx.Response(400, json={"error": "'model' query parameter is required"})
        return httpx.Response(200, json={"model": request.url.params["model"], "voices": ["a", "b"]})
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(discovery.fetch_voices("http://omlx/v1", "k")) is None                         # without a model oMLX refuses
    assert asyncio.run(discovery.fetch_voices("http://omlx/v1", "k", None, "Kokoro-82M-bf16")) == ["a", "b"]
    assert seen == [None, "Kokoro-82M-bf16"]


# ---- unified endpoint: a directly exposed TTS model gets the same discovered-voices checklist
def test_unified_direct_tts_model_voice_selection(client, up):
    up.replies["kokoro"] = (200, {"voices": ["a", "b", "c"]})
    with Session(db.engine) as s:
        ep = Endpoint(name=f"ep-udirect-{time.time_ns()}", base_url="http://kokoro/v1", status="online"); s.add(ep); s.commit(); s.refresh(ep)
        tts = Model(endpoint_id=ep.id, upstream_id="kokoro", capabilities=["tts"], max_slots=2, enabled=True, voices=["a", "b", {"id": "c", "name": "Charlie"}]); s.add(tts)
        chat = Model(endpoint_id=ep.id, upstream_id="llm", capabilities=["chat"], max_slots=2, enabled=True); s.add(chat)
        s.commit(); s.refresh(tts); s.refresh(chat)
        ep_id, tts_id, chat_id = ep.id, tts.id, chat.id
    health.status[ep_id] = "online"
    put = lambda voices, **kw: client.put("/api/unified", json={"enabled": True, "gateways": [], "models": [  # noqa: E731
        {"model_id": tts_id, "name": "kokoro-direct", "voices": voices, **kw}, {"model_id": chat_id, "name": "llm-direct", "voices": ["ignored"]}]}).json()
    try:
        v = put(["b", " b ", "", "zzz"])
        row = next(m for m in v["models"] if m["name"] == "kokoro-direct")
        assert row["voices"] == ["b", "zzz"] and row["voice_options"] == ["a", "b", "c"]            # selection kept; discovered ids offered as options
        assert next(m for m in v["models"] if m["name"] == "llm-direct")["voices"] == []             # only TTS models carry voices
        r = client.get("/unified/v1/audio/voices?model=kokoro-direct")
        assert r.json() == {"voices": ["b", "zzz"]}                                                  # the admin's choice, not the upstream's list
        assert client.get("/unified/v1/audio/voices").json() == {"voices": ["b", "zzz"]}
        put([])                                                                                      # nothing selected: the upstream's own list again
        proxy._voices_cache.clear()
        assert client.get("/unified/v1/audio/voices?model=kokoro-direct").json() == {"voices": ["a", "b", "c"]}
    finally:
        client.put("/api/unified", json={"enabled": False, "gateways": [], "models": []})
