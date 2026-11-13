import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import db, discovery, proxy
from app.main import app

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return json.load(open(os.path.join(FIX, f"opencode_{name}_models.json")))["data"]


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def test_provider_detection():
    d = discovery.detect_provider
    assert d("https://opencode.ai/inference/v1") == "opencode-zen"
    assert d("https://opencode.ai/inference/go/v1") == "opencode-go"
    assert d("https://opencode.ai/zen/v1/") == "opencode-zen" and d("https://opencode.ai/zen/go/v1") == "opencode-go"
    assert d("https://opencode.ai/inference/go/openai/v1") == "opencode-go"
    for other in ("http://localhost:8000/v1", "https://api.openai.com/v1", "https://opencode.ai.evil.com/inference/v1", "https://opencode.ai/docs", "", "not a url"):
        assert d(other) is None, other


def test_capabilities_from_metadata():
    zen = {m["id"]: m for m in fixture("zen")}
    caps = lambda i: discovery.caps_from_opencode(zen[i])  # noqa: E731
    assert caps("big-pickle") == {"chat", "tools", "reasoning"}                       # text only
    assert "vision" in caps("claude-opus-5-5") and "tools" in caps("claude-opus-5-5")
    for m in zen.values():                                                            # whatever the catalog says, the mapping agrees with it
        c = discovery.caps_from_opencode(m)
        assert ("vision" in c) == any(str(x).startswith("image") for x in m["modalities"]["input"])
        assert ("tools" in c) == bool(m["capabilities"].get("tools", {}).get("supported"))
        assert ("reasoning" in c) == bool(m["capabilities"].get("reasoning", {}).get("supported"))
    assert discovery.caps_from_opencode({"id": "x"}) == {"chat"}                      # no metadata -> plain chat
    assert discovery.caps_from_opencode({"capabilities": {"tools": {"supported": False}}, "type": "chat"}) == {"chat"}


def test_context_uses_the_real_window():
    m = {x["id"]: x for x in fixture("zen")}["big-pickle"]
    assert m["limit"]["input"] < m["limit"]["context"]
    assert discovery.extract_context(m) == m["limit"]["context"]
    assert discovery.extract_context({"limit": {"input": 5000}}) == 5000
    assert discovery.extract_context({"limit": "bogus"}) is None


def test_discover_never_probes_hosted_catalogs(monkeypatch):
    async def fake_fetch(client, base, key):
        return [dict(x) for x in fixture("go")]

    def boom(*a, **k):
        raise AssertionError("a paid/local probe was attempted on a hosted catalog")
    monkeypatch.setattr(discovery, "fetch_models", fake_fetch)
    for name in ("probe_props", "probe_ollama_show", "probe_embedding", "probe_vision", "probe_tools", "probe_reasoning", "probe_chat"):
        monkeypatch.setattr(discovery, name, boom)
    found = asyncio.run(discovery.discover("https://opencode.ai/inference/go/v1", "key", probe=True))
    by = {d.upstream_id: d for d in found}
    assert set(by) == {m["id"] for m in fixture("go")}
    assert all(d.context_length for d in found) and all(set(d.sources.values()) == {"metadata"} for d in found)
    assert by["beam-free"].free and not by["glm-5.3"].free
    assert "vision" in by["claude-haiku-5-5"].capabilities


def test_session_header_is_stable_per_conversation_and_required_headers_present():
    convo = {"messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "first question"}]}
    longer = {"messages": convo["messages"] + [{"role": "assistant", "content": "a"}, {"role": "user", "content": "follow-up"}]}
    other = {"messages": [{"role": "user", "content": "something else"}]}
    h = lambda body, hdr=None: proxy.provider_headers("opencode-go", hdr or {}, body)  # noqa: E731
    assert h(convo)["x-opencode-session"] == h(longer)["x-opencode-session"]          # same conversation -> same id (prompt-cache routing)
    assert h(convo)["x-opencode-session"] != h(other)["x-opencode-session"]
    assert h(convo, {"x-sg-session": "abc"})["x-opencode-session"] != h(convo)["x-opencode-session"]
    assert h(convo, {"x-sg-session": "abc"}) == h(other, {"x-sg-session": "abc"})     # an explicit session wins
    assert h({"user": "u1", "messages": []})["x-opencode-session"] == h({"user": "u1", "messages": [{"role": "user", "content": "z"}]})["x-opencode-session"]
    out = h(convo)
    assert out["x-opencode-session"].startswith("sg-") and out["User-Agent"].startswith("SmartGateway/")
    assert proxy.provider_headers("something-else", {}, convo) == {}


# ---- API (network replaced by fixtures)
def patch_network(monkeypatch, models, key_result):
    async def ping(base, key):
        return True, None

    async def fetch(client, base, key):
        return [dict(x) for x in models]

    async def check(base, key, model, provider=None):
        check.calls.append((base, model, provider))
        return key_result
    check.calls = []
    monkeypatch.setattr(discovery, "ping", ping)
    monkeypatch.setattr(discovery, "fetch_models", fetch)
    monkeypatch.setattr(discovery, "check_key", check)
    return check


def test_add_preset_endpoint(client, monkeypatch):
    check = patch_network(monkeypatch, fixture("zen"), (True, ""))
    r = client.post("/api/endpoints", json={"name": "zen", "provider": "opencode-zen", "api_key": "k-zen"})
    assert r.status_code == 200, r.text
    ep = r.json()
    assert ep["provider"] == "opencode-zen" and ep["base_url"] == "https://opencode.ai/inference/v1" and ep["slot_source"] == "none"
    by = {m["upstream_id"]: m for m in ep["models"]}
    assert set(by) == {m["id"] for m in fixture("zen")}
    assert all(m["max_slots"] == 8 for m in ep["models"]) and "vision" in by["gpt-5.5"]["capabilities"] and by["glm-5.3"]["context_length"]
    assert len(check.calls) == 1 and check.calls[0][1] in ("big-pickle", "deepseek-v4.1-flash-free") and check.calls[0][2] == "opencode-zen"
    assert "k-zen" not in json.dumps(ep)
    client.delete(f"/api/endpoints/{ep['id']}")


def test_provider_detected_from_a_typed_url_and_rescan_keeps_it(client, monkeypatch):
    patch_network(monkeypatch, fixture("go"), (True, ""))
    ep = client.post("/api/endpoints", json={"name": "go", "base_url": "https://opencode.ai/inference/go/v1/", "api_key": "k"}).json()
    assert ep["provider"] == "opencode-go" and ep["base_url"] == "https://opencode.ai/inference/go/v1"
    again = client.post(f"/api/endpoints/{ep['id']}/rescan?probe=true").json()
    assert again["provider"] == "opencode-go" and len(again["models"]) == len(fixture("go"))
    client.delete(f"/api/endpoints/{ep['id']}")


def test_rejected_key_leaves_nothing_behind(client, monkeypatch):
    patch_network(monkeypatch, fixture("zen"), (False, "HTTP 401"))
    before = len(client.get("/api/endpoints").json())
    r = client.post("/api/endpoints", json={"name": "bad", "provider": "opencode-zen", "api_key": "wrong"})
    assert r.status_code == 400 and "rejected" in r.json()["detail"] and "OpenCode Zen" in r.json()["detail"]
    assert len(client.get("/api/endpoints").json()) == before


def test_key_check_is_skipped_without_a_key_or_when_inconclusive(client, monkeypatch):
    check = patch_network(monkeypatch, fixture("zen"), (None, "HTTP 429"))
    no_key = client.post("/api/endpoints", json={"name": "nokey", "provider": "opencode-zen"})
    assert no_key.status_code == 200 and check.calls == []                          # catalog + free models work without a key
    inconclusive = client.post("/api/endpoints", json={"name": "maybe", "provider": "opencode-zen", "api_key": "k"})
    assert inconclusive.status_code == 200 and len(check.calls) == 1                # quota reached etc. is not a bad key
    for e in (no_key.json(), inconclusive.json()):
        client.delete(f"/api/endpoints/{e['id']}")


def test_unknown_provider_and_regular_endpoints_unchanged(client, monkeypatch):
    assert client.post("/api/endpoints", json={"name": "x", "provider": "nope", "base_url": "http://h/v1"}).status_code == 400
    assert discovery.detect_provider("http://192.168.1.5:8000/v1") is None
