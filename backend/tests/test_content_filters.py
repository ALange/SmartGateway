import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db, proxy
from app.guardrails import library, pipeline
from app.main import app
from app.models import ContentFilter, Endpoint, FilterGroup, FilterGroupMember, GatewayModel, Model
from app.router import health


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def uniq(p):
    return f"{p}-{time.time_ns()}"


def mk_filter(client, **kw):
    body = {"name": uniq("f"), "pattern": "swordfish", "stage": "input", "action": "block", **kw}
    r = client.post("/api/content-filters", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def mk_group(client, **kw):
    r = client.post("/api/content-filter-groups", json={"name": uniq("g"), **kw})
    assert r.status_code == 200, r.text
    return r.json()


def listing(client):
    d = client.get("/api/content-filters").json()
    return {f["id"]: f for f in d["filters"]}, {g["id"]: g for g in d["groups"]}


def test_crud_and_many_to_many_groups(client):
    f1, f2 = mk_filter(client), mk_filter(client, pattern="falcon")
    g1, g2 = mk_group(client, filter_ids=[f1["id"], f2["id"]]), mk_group(client, filter_ids=[f1["id"]])
    fs, gs = listing(client)
    assert fs[f1["id"]]["group_ids"] == sorted([g1["id"], g2["id"]]) and fs[f2["id"]]["group_ids"] == [g1["id"]]
    assert gs[g1["id"]]["filter_ids"] == sorted([f1["id"], f2["id"]])
    # edit a filter and its memberships
    r = client.put(f"/api/content-filters/{f1['id']}", json={**f1, "name": uniq("renamed"), "pattern": "marlin", "group_ids": [g2["id"]]})
    assert r.status_code == 200
    fs, gs = listing(client)
    assert fs[f1["id"]]["pattern"] == "marlin" and fs[f1["id"]]["group_ids"] == [g2["id"]] and gs[g1["id"]]["filter_ids"] == [f2["id"]]
    # edit a group's members
    client.put(f"/api/content-filter-groups/{g2['id']}", json={"name": g2["name"], "filter_ids": []})
    assert listing(client)[1][g2["id"]]["filter_ids"] == []
    # deleting a filter removes it from its groups; deleting a group keeps the filters
    assert client.delete(f"/api/content-filters/{f2['id']}").status_code == 200
    assert listing(client)[1][g1["id"]]["filter_ids"] == []
    assert client.delete(f"/api/content-filter-groups/{g1['id']}").status_code == 200
    assert f1["id"] in listing(client)[0]


def test_validation(client):
    f = mk_filter(client)
    assert client.post("/api/content-filters", json={"name": f["name"].upper(), "pattern": "x"}).status_code == 409     # names are unique, case-insensitively
    assert client.post("/api/content-filters", json={"name": uniq("x"), "pattern": "("}).status_code == 400
    assert client.post("/api/content-filters", json={"name": uniq("x"), "pattern": ".*"}).status_code == 400            # matches everything / nothing
    assert client.post("/api/content-filters", json={"name": uniq("x"), "pattern": "a", "stage": "nope"}).status_code == 400
    assert client.post("/api/content-filters", json={"name": uniq("x"), "pattern": "a", "group_ids": [987654]}).status_code == 400
    assert client.post("/api/content-filters", json={"name": "  ", "pattern": "a"}).status_code == 400
    assert client.post("/api/content-filter-groups", json={"name": uniq("g"), "filter_ids": [987654]}).status_code == 400
    t = client.post("/api/content-filters/test", json={"pattern": "sword(fish)", "text": "a swordfish and a Swordfish"}).json()
    assert t["count"] == 2 and t["matches"][0]["text"] == "swordfish"
    assert client.post("/api/content-filters/test", json={"pattern": "(", "text": "x"}).status_code == 400


# ------------------------------------------------------------------ gateway integration
@pytest.fixture
def upstream(monkeypatch):
    state = {"seen": [], "reply": "hello"}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        state["seen"].append(body)
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "model": body["model"],
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": state["reply"]}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})

    monkeypatch.setattr(proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return state


def mk_gateway(client, guardrails):
    with Session(db.engine) as s:
        ep = Endpoint(name="cf-ep", base_url="http://fake/v1", status="online")
        s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="up", capabilities=["chat"], max_slots=4, context_length=32000)
        s.add(m); s.commit(); s.refresh(m)
        ep_id, m_id = ep.id, m.id
    health.status[ep_id] = "online"
    body = {"name": uniq("gw"), "slug": uniq("cf"), "models": [{"public_name": "mm", "guardrails": guardrails, "members": [{"model_id": m_id}]}]}
    r = client.post("/api/gateways", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def chat(client, gw, content):
    return client.post(f"/api/admin-gw/{gw['slug']}/v1/chat/completions", json={"model": "mm", "messages": [{"role": "user", "content": content}]})


def test_gateway_picks_a_group_and_library_edits_apply_without_resaving(client, upstream):
    f = mk_filter(client, pattern="swordfish", action="block")
    g = mk_group(client, filter_ids=[f["id"]])
    gw = mk_gateway(client, {"filters": {"mode": "on", "group_ids": [g["id"]]}})
    assert chat(client, gw, "the swordfish").status_code == 400 and upstream["seen"] == []
    assert chat(client, gw, "hello there").status_code == 200
    # change the filter in the library: the gateway follows, no re-save of the gateway
    client.put(f"/api/content-filters/{f['id']}", json={**f, "pattern": "marlin", "group_ids": [g["id"]]})
    assert chat(client, gw, "the swordfish").status_code == 200
    assert chat(client, gw, "the marlin").status_code == 400
    # a disabled filter is not applied
    client.put(f"/api/content-filters/{f['id']}", json={**f, "pattern": "marlin", "enabled": False, "group_ids": [g["id"]]})
    assert chat(client, gw, "the marlin").status_code == 200
    # adding a second filter to the group extends the gateway
    f2 = mk_filter(client, pattern="falcon", action="redact")
    client.put(f"/api/content-filter-groups/{g['id']}", json={"name": g["name"], "filter_ids": [f["id"], f2["id"]]})
    chat(client, gw, "a falcon flies")
    assert upstream["seen"][-1]["messages"][0]["content"] == "a [FILTERED] flies"


def test_single_filters_mode_off_and_unknown_refs(client, upstream):
    f = mk_filter(client, pattern="albatross")
    gw = mk_gateway(client, {"filters": {"mode": "on", "filter_ids": [f["id"]]}})
    assert chat(client, gw, "albatross").status_code == 400
    off = mk_gateway(client, {"filters": {"mode": "off", "filter_ids": [f["id"]]}})
    assert off["models"][0]["guardrails"] is not None
    assert chat(client, off, "albatross").status_code == 200                     # selection kept but switched off
    body = {"name": "x", "slug": uniq("bad"), "models": [{"public_name": "mm", "guardrails": {"filters": {"mode": "on", "group_ids": [987654]}}, "members": []}]}
    r = client.post("/api/gateways", json=body)
    assert r.status_code == 400 and "group 987654" in r.json()["detail"]


def test_output_filter_from_library_blocks_and_changes_cache_key(client, upstream):
    f = mk_filter(client, pattern="forbidden", stage="output", action="block")
    gw = mk_gateway(client, {"filters": {"mode": "on", "filter_ids": [f["id"]]}})
    upstream["reply"] = "this is forbidden"
    r = chat(client, gw, "hi")
    assert r.status_code == 422 and r.json()["error"]["type"] == "guardrail_blocked"
    upstream["reply"] = "fine"
    assert chat(client, gw, "hi").status_code == 200
    cfg = pipeline.normalize({"filters": {"mode": "on", "filter_ids": [f["id"]]}})
    a = pipeline.fingerprint(cfg, library.resolve(cfg))
    client.put(f"/api/content-filters/{f['id']}", json={**f, "pattern": "other"})
    assert pipeline.fingerprint(cfg, library.resolve(cfg)) != a                  # editing the filter changes the cache key


def test_delete_is_protected_while_in_use(client, upstream):
    f, g = mk_filter(client), None
    g = mk_group(client, filter_ids=[f["id"]])
    gw = mk_gateway(client, {"filters": {"mode": "on", "group_ids": [g["id"]], "filter_ids": [f["id"]]}})
    r = client.delete(f"/api/content-filters/{f['id']}")
    assert r.status_code == 409 and gw["name"] in r.json()["detail"]["used_by"][0]
    assert client.delete(f"/api/content-filter-groups/{g['id']}").status_code == 409
    assert client.delete(f"/api/content-filters/{f['id']}?force=true").status_code == 200
    assert client.delete(f"/api/content-filter-groups/{g['id']}?force=true").status_code == 200
    assert chat(client, gw, "swordfish").status_code == 200                       # the dangling reference is simply ignored


def test_resolve_ignores_missing_and_disabled(client):
    f = mk_filter(client)
    cfg = pipeline.normalize({"filters": {"mode": "on", "filter_ids": [f["id"], 987654]}})
    assert [r["id"] for r in library.resolve(cfg)] == [f["id"]]
    client.put(f"/api/content-filters/{f['id']}", json={**f, "enabled": False})
    assert library.resolve(cfg) == []


# ------------------------------------------------------------------ migration of inline rules
def test_inline_rules_are_migrated_once(client):
    gw = mk_gateway(client, None)
    old = {"filters": {"mode": "on", "rules": [{"name": "legacy-a", "pattern": "alpha", "stage": "input", "action": "block"},
                                                {"name": "legacy-b", "pattern": "beta", "stage": "output", "action": "redact"}]}}
    with Session(db.engine) as s:
        gm = s.get(GatewayModel, gw["models"][0]["id"])
        gm.guardrails = old
        s.add(gm); s.commit()
    db._migrate_inline_filters()
    with Session(db.engine) as s:
        cfg = s.get(GatewayModel, gw["models"][0]["id"]).guardrails
    assert "rules" not in cfg["filters"] and cfg["filters"]["mode"] == "on" and len(cfg["filters"]["group_ids"]) == 1
    rules = library.resolve(pipeline.normalize(cfg))
    assert sorted(r["name"] for r in rules) == ["legacy-a", "legacy-b"]
    before = listing(client)
    db._migrate_inline_filters()                                                   # idempotent
    after = listing(client)
    assert before[0].keys() == after[0].keys() and before[1].keys() == after[1].keys()


# ------------------------------------------------------------------ preset library
def test_presets_are_seeded_once_and_install_only_adds_missing(client):
    from app.guardrails import presets

    fs, gs = listing(client)
    by_name = {f["name"]: f for f in fs.values()}
    gby = {g["name"]: g for g in gs.values()}
    assert all(p["name"] in by_name for p in presets.FILTERS) and all(n in gby for n in presets.GROUPS)
    pl = gby["PII – Poland"]
    assert {fs[i]["name"] for i in pl["filter_ids"]} == {p["name"] for p in presets.FILTERS if "PII – Poland" in p["groups"]}
    assert by_name["PL – PESEL"]["validator"] == "pesel" and by_name["PL – PESEL"]["action"] == "redact" and by_name["PL – PESEL"]["stage"] == "input"
    # a second start does not touch anything; the install endpoint finds nothing missing
    before = len(fs)
    db._seed_presets()
    assert client.post("/api/content-filters/presets/install").json() == {"added_filters": 0, "added_groups": 0}
    assert len(listing(client)[0]) == before
    # the user edits one preset and deletes another: a restart keeps both decisions, install only restores the deleted one
    pesel, nip = by_name["PL – PESEL"], by_name["PL – NIP"]
    client.put(f"/api/content-filters/{pesel['id']}", json={**pesel, "action": "block", "group_ids": pesel["group_ids"]})
    assert client.delete(f"/api/content-filters/{nip['id']}").status_code == 200
    db._seed_presets()
    assert "PL – NIP" not in {f["name"] for f in listing(client)[0].values()}
    assert client.post("/api/content-filters/presets/install").json() == {"added_filters": 1, "added_groups": 0}
    fs, gs = listing(client)
    by_name = {f["name"]: f for f in fs.values()}
    assert by_name["PL – PESEL"]["action"] == "block"                               # your edit survived
    assert sorted(g for g in by_name["PL – NIP"]["group_ids"]) == sorted(g["id"] for g in gs.values() if g["name"] in presets.FILTERS[1]["groups"])
    client.put(f"/api/content-filters/{pesel['id']}", json={**pesel, "action": "redact", "group_ids": pesel["group_ids"]})   # put it back for later tests


def test_country_group_redacts_valid_ids_and_leaves_invalid_ones(client, upstream):
    _, gs = listing(client)
    pl = next(g for g in gs.values() if g["name"] == "PII – Poland")
    gw = mk_gateway(client, {"filters": {"mode": "on", "group_ids": [pl["id"]]}})
    r = chat(client, gw, "Mój PESEL to 44051401359, tel. +48 501 234 567, a to zły numer 44051401358.")
    assert r.status_code == 200
    sent = upstream["seen"][-1]["messages"][0]["content"]
    assert "44051401359" not in sent and "501 234 567" not in sent
    assert "44051401358" in sent and sent.count("[FILTERED]") == 2


def test_gdpr_special_categories_group_in_several_languages(client, upstream):
    _, gs = listing(client)
    g9 = next(g for g in gs.values() if g["name"] == "GDPR – Special categories (Art. 9)")
    gw = mk_gateway(client, {"filters": {"mode": "on", "group_ids": [g9["id"]]}})
    chat(client, gw, "Pacjent ma cukrzycę, Mitarbeiter ist schwanger, and he is a Muslim.")
    sent = upstream["seen"][-1]["messages"][0]["content"]
    assert sent.count("[FILTERED]") >= 3 and "cukrzyc" not in sent and "schwanger" not in sent and "Muslim" not in sent
    chat(client, gw, "Please summarise the quarterly report.")
    assert upstream["seen"][-1]["messages"][0]["content"] == "Please summarise the quarterly report."


def test_validator_is_saved_validated_and_used_by_the_test_endpoint(client):
    assert any(v["name"] == "pesel" for v in client.get("/api/content-filters").json()["validators"])
    assert client.post("/api/content-filters", json={"name": uniq("v"), "pattern": r"\d{11}", "validator": "nope"}).status_code == 400
    f = mk_filter(client, pattern=r"\d{11}", validator="pesel")
    assert f["validator"] == "pesel"
    t = client.post("/api/content-filters/test", json={"pattern": r"\d{11}", "validator": "pesel", "text": "44051401359 and 44051401358"}).json()
    assert t["count"] == 1 and t["matches"][0]["text"] == "44051401359"
