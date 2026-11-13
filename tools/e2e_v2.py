#!/usr/bin/env python3
"""End-to-end checks for v2 features. Spawns mock upstreams + a throw-away gateway (own data dir).

Run with a Python that has the backend requirements installed:
    python tools/e2e_v2.py
Never talks to any real endpoint.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
procs, results = [], []
GW, GW_AUTH = "http://127.0.0.1:8098", "http://127.0.0.1:8097"


def spawn(cmd, env, cwd):
    p = subprocess.Popen(cmd, env={**os.environ, **env}, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    return p


def wait(url, tries=50):
    for _ in range(tries):
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise RuntimeError(f"not up: {url}")


def check(name, cond, detail=""):
    results.append(cond)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def mock(port, name, models, **env):
    spawn([PY, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "error"],
          {"MOCK_NAME": name, "MODELS": models, **{k: str(v) for k, v in env.items()}}, os.path.join(ROOT, "tools", "mock-llm"))
    wait(f"http://127.0.0.1:{port}/stats")


def main():
    data = tempfile.mkdtemp(prefix="sge2e-")
    mock(9101, "small", "small-m", CTX=800, EXPOSE_PROPS=1, EXPOSE_META=1, SLOTS=2, TOKEN_DELAY=0.4)
    mock(9102, "big", "big-m:tools", CTX=100000, EXPOSE_PROPS=1, EXPOSE_META=1, SLOTS=2)
    mock(9103, "nometa", "nometa-m", CTX=800, SLOTS=2)
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8098", "--log-level", "error"],
          {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "5", "SG_FALLBACK_GRACE": "1"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=30)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    models = {}
    for port, name in ((9101, "A-small"), (9102, "B-big"), (9103, "C-nometa")):
        ep = api("POST", "/endpoints", json={"name": name, "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "sk-test-secret", "probe": False})
        models[name[0]] = ep["models"][0]
        print(f"  endpoint {name}: ctx={ep['models'][0]['context_length']} slots={ep['models'][0]['max_slots']} caps={ep['models'][0]['capabilities']} slot_source={ep.get('slot_source')}")
    A, B, C = (models[k]["id"] for k in "ABC")
    check("context length detected from meta/props", models["A"]["context_length"] == 800 and models["B"]["context_length"] == 100000)
    check("tools capability detected from /props chat_template_caps", "tools" in models["B"]["capabilities"] and "tools" not in models["A"]["capabilities"])
    check("slots detected from /props", models["A"]["max_slots"] == 2)
    check("upstream key encrypted at rest", all(v.startswith("enc:") for (v,) in sqlite3.connect(os.path.join(data, "smartgateway.db")).execute("select api_key from endpoint")))

    def gateway(slug, model, members, **kw):
        return api("POST", "/gateways", json={"name": slug, "slug": slug, "models": [{"public_name": model, "members": members, **kw.pop("mkw", {})}], **kw})

    def chat(slug, model, content="hi", extra=None, headers=None, stream=False, base=None):
        body = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 4, **(extra or {})}
        if stream:
            body["stream"] = True
        return c.post(f"/gw/{slug}/v1/chat/completions", json=body, headers=headers or {})

    print("\n[A1] context-fit routing")
    gateway("g1", "ctx", [{"model_id": A}, {"model_id": B}])
    big_prompt = "word " * 900  # ~4500 chars ~ 1300 tokens > 800
    r = chat("g1", "ctx", big_prompt)
    check("long prompt goes to the big-context model", r.status_code == 200 and r.headers["x-sg-upstream"] == "big-m", r.text[:120])
    r = chat("g1", "ctx", "word " * 120000)
    check("prompt larger than every window -> 400 context_length_exceeded", r.status_code == 400 and r.json()["error"]["type"] == "context_length_exceeded", r.text[:120])

    print("[A1b] overflow retry when context is unknown")
    gateway("g2", "ov", [{"model_id": C, "priority": 0}, {"model_id": B, "priority": 1}], strategy="priority_failover")
    r = chat("g2", "ov", big_prompt)
    check("upstream context error -> retried on larger member", r.status_code == 200 and r.headers["x-sg-upstream"] == "big-m" and r.headers["x-sg-attempts"] == "2", f"{r.status_code} {dict(r.headers)}")

    print("[A2] external slot sync")
    gateway("g3", "ss", [{"model_id": A}, {"model_id": B}])
    httpx.post("http://127.0.0.1:9101/admin/external/2")
    time.sleep(4.5)
    live = api("GET", "/live")["models"]
    check("external busy slots visible", live[str(A)]["external"] == 2, str(live[str(A)]))
    ups = {chat("g3", "ss").headers["x-sg-upstream"] for _ in range(4)}
    check("routing avoids externally-busy model", ups == {"big-m"}, str(ups))
    httpx.post("http://127.0.0.1:9101/admin/external/0")
    time.sleep(3)

    print("[A3] fallback members")
    gateway("g9", "fb", [{"model_id": A}, {"model_id": B, "is_fallback": True}])
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(5) as ex:
        slow = [ex.submit(chat, "g9", "fb") for _ in range(2)]
        time.sleep(0.3)
        t0 = time.time()
        quick = chat("g9", "fb")
        waited = time.time() - t0
        [f.result() for f in slow]
    check("fallback used only after grace (~1s) when primary is full", quick.headers["x-sg-upstream"] == "big-m" and 0.8 < waited < 3, f"{quick.headers.get('x-sg-upstream')} {waited:.1f}s")
    ups = {chat("g9", "fb").headers["x-sg-upstream"] for _ in range(3)}
    check("fallback idle while primary is free", ups == {"small-m"}, str(ups))

    print("[A4] sticky routing")
    gateway("g7", "st", [{"model_id": A}, {"model_id": B}], strategy="weighted_round_robin", mkw={"sticky": True})
    gateway("g7b", "nst", [{"model_id": A}, {"model_id": B}], strategy="weighted_round_robin")
    s1 = {chat("g7", "st", headers={"X-SG-Session": "sess1"}).headers["x-sg-upstream"] for _ in range(6)}
    s2 = {chat("g7b", "nst").headers["x-sg-upstream"] for _ in range(6)}
    check("same session sticks to one member", len(s1) == 1, str(s1))
    check("without sticky the round-robin alternates", len(s2) == 2, str(s2))

    print("[A5] tiered routing + tools preference")
    gateway("g8", "tier", [{"model_id": A, "tier": 1}, {"model_id": B, "tier": 3}], strategy="tiered")
    check("small prompt -> tier 1", chat("g8", "tier").headers["x-sg-upstream"] == "small-m")
    check("tools request escalates", chat("g8", "tier", extra={"tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}], "reasoning_effort": "high"}).headers["x-sg-upstream"] == "big-m")
    gateway("g5", "tl", [{"model_id": A}, {"model_id": B}])
    r = chat("g5", "tl", extra={"tools": [{"type": "function", "function": {"name": "get_x", "parameters": {"type": "object"}}}]})
    check("tools request goes only to tool-capable model and returns tool_calls", r.headers["x-sg-upstream"] == "big-m" and r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_x", r.text[:150])

    print("[B1] API keys + limits")
    g4 = gateway("g4", "k", [{"model_id": B}])
    k = api("POST", f"/gateways/{g4['id']}/keys", json={"name": "limited", "rpm_limit": 3})
    plain = k["key"]
    check("key returned once, hash stored", plain.startswith("sg-") and "key_hash" not in k and plain not in json.dumps(api("GET", f"/gateways/{g4['id']}/keys")))
    check("no key -> 401", chat("g4", "k").status_code == 401)
    codes = [chat("g4", "k", headers={"Authorization": f"Bearer {plain}"}).status_code for _ in range(5)]
    check("rpm limit: 3x200 then 429", codes == [200, 200, 200, 429, 429], str(codes))
    r = chat("g4", "k", headers={"Authorization": f"Bearer {plain}"})
    check("429 carries Retry-After", "retry-after" in r.headers)
    r = c.post("/v1/chat/completions", json={"model": "k", "messages": [{"role": "user", "content": "x"}]}, headers={"x-api-key": plain})
    check("plain /v1 resolves gateway from key (still rate limited)", r.status_code == 429)
    k2 = api("POST", f"/gateways/{g4['id']}/keys", json={"name": "models", "allowed_models": ["other"]})
    check("allowed_models enforced (403)", chat("g4", "k", headers={"Authorization": f"Bearer {k2['key']}"}).status_code == 403)
    api("PATCH", f"/keys/{k2['id']}", json={"name": "models", "enabled": False})
    check("disabled key -> 401", chat("g4", "k", headers={"Authorization": f"Bearer {k2['key']}"}).status_code == 401)
    k3 = api("POST", f"/gateways/{g4['id']}/keys", json={"name": "quota", "daily_token_quota": 1})
    h3 = {"Authorization": f"Bearer {k3['key']}"}
    chat("g4", "k", headers=h3)
    time.sleep(0.5)
    check("daily token quota exhausted -> 429 insufficient_quota", chat("g4", "k", headers=h3).json()["error"]["type"] == "insufficient_quota")

    print("[C] API surfaces")
    r = c.post("/gw/g1/v1/messages", json={"model": "ctx", "max_tokens": 5, "system": "s", "messages": [{"role": "user", "content": "hello"}]})
    j = r.json()
    check("Anthropic /v1/messages non-stream", r.status_code == 200 and j["type"] == "message" and j["content"][0]["type"] == "text" and j["usage"]["output_tokens"] > 0 and j["model"] == "ctx", r.text[:200])
    with c.stream("POST", "/gw/g1/v1/messages", json={"model": "ctx", "max_tokens": 5, "stream": True, "messages": [{"role": "user", "content": "hello"}]}) as s:
        ev = [l[7:] for l in s.iter_lines() if l.startswith("event:")]
    check("Anthropic stream event sequence", ev[0] == "message_start" and "content_block_delta" in ev and ev[-2:] == ["message_delta", "message_stop"], str(ev))
    r = c.post("/gw/g1/v1/responses", json={"model": "ctx", "input": "hello", "max_output_tokens": 5})
    check("Responses API non-stream", r.status_code == 200 and r.json()["output"][0]["content"][0]["type"] == "output_text" and r.json()["status"] == "completed", r.text[:200])
    with c.stream("POST", "/gw/g1/v1/responses", json={"model": "ctx", "input": "hello", "stream": True}) as s:
        ev = [l[7:] for l in s.iter_lines() if l.startswith("event:")]
    check("Responses stream events", ev[0] == "response.created" and ev[-1] == "response.completed" and "response.output_text.delta" in ev, str(ev[:3]))
    r = c.post("/gw/nope/v1/messages", json={"model": "x", "messages": []})
    check("Anthropic-shaped error", r.status_code == 404 and r.json()["type"] == "error")
    check("count_tokens estimate", c.post("/gw/g1/v1/messages/count_tokens", json={"model": "ctx", "messages": [{"role": "user", "content": "x" * 350}]}).json()["input_tokens"] >= 90)

    print("[C] defaults / cap / cache")
    gateway("g6", "dflt", [{"model_id": B}], mkw={"cache_enabled": True, "max_tokens_cap": 3, "system_prompt": "SYS", "default_params": {"temperature": 0}})
    r = c.post("/gw/g6/v1/chat/completions", json={"model": "dflt", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50})
    check("max_tokens_cap clamps request", r.json()["usage"]["completion_tokens"] == 3, r.text[:200])
    before = httpx.get("http://127.0.0.1:9102/stats").json()["total"]
    r1 = c.post("/gw/g6/v1/chat/completions", json={"model": "dflt", "messages": [{"role": "user", "content": "cache me"}]})
    r2 = c.post("/gw/g6/v1/chat/completions", json={"model": "dflt", "messages": [{"role": "user", "content": "cache me"}]})
    after = httpx.get("http://127.0.0.1:9102/stats").json()["total"]
    check("default temperature=0 enables exact cache hit", r2.headers.get("x-sg-cache") == "hit" and after - before == 1 and r1.json()["choices"] == r2.json()["choices"], f"{r1.headers.get('x-sg-cache')} {r2.headers.get('x-sg-cache')} {after-before}")
    check("system prompt prepended (upstream saw it)", httpx.get("http://127.0.0.1:9102/stats").json()["last_prompt_chars"] >= 3)

    print("[C] admin test route")
    r = c.post("/api/admin-gw/g4/v1/chat/completions", json={"model": "k", "messages": [{"role": "user", "content": "x"}], "max_tokens": 2})
    check("admin-gw bypasses API key requirement", r.status_code == 200)

    print("[B] admin login")
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8097", "--log-level", "error"],
          {"SG_DATA_DIR": tempfile.mkdtemp(prefix="sge2e-auth-"), "SG_ADMIN_PASSWORD": "pw-123"}, os.path.join(ROOT, "backend"))
    wait(GW_AUTH + "/healthz")
    a = httpx.Client(base_url=GW_AUTH, timeout=10)
    check("session info says auth required", a.get("/api/session").json() == {"auth_required": True, "authenticated": False})
    check("API blocked without login", a.get("/api/endpoints").status_code == 401)
    check("UI shell stays reachable", a.get("/healthz").status_code == 200)
    check("wrong password -> 401", a.post("/api/login", json={"username": "admin", "password": "x"}).status_code == 401)
    lr = a.post("/api/login", json={"username": "admin", "password": "pw-123"})
    check("login ok sets HttpOnly cookie", lr.status_code == 200 and "httponly" in lr.headers.get("set-cookie", "").lower(), lr.headers.get("set-cookie", ""))
    check("API works with session cookie", a.get("/api/endpoints").status_code == 200)
    check("Bearer password still works for scripts", httpx.get(GW_AUTH + "/api/endpoints", headers={"Authorization": "Bearer pw-123"}).status_code == 200)
    a.post("/api/logout")
    a.cookies.clear()
    for _ in range(6):
        last = httpx.post(GW_AUTH + "/api/login", json={"username": "admin", "password": "bad"}).status_code
    check("login throttling after repeated failures", last == 429, str(last))


try:
    main()
except Exception:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    results.append(False)
finally:
    for p in procs:
        p.terminate()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
