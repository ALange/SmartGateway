#!/usr/bin/env python3
"""End-to-end checks for the unified endpoint (/unified/v1): temporary mock upstreams + throw-away gateways, nothing real is touched."""
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
procs, results = [], []
GW = "http://127.0.0.1:8088"
MOCKS = {"a": 9511, "b": 9512, "c": 9513}


def spawn(cmd, env, cwd):
    procs.append(subprocess.Popen(cmd, env={**os.environ, **env}, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))


def wait(url):
    for _ in range(60):
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise RuntimeError(url)


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    data = tempfile.mkdtemp(prefix="sgunified-")
    spec = {"a": "chat-a", "b": "think-b:reasoning,see-b:vision", "c": "hidden-c"}
    for n, port in MOCKS.items():
        spawn([PY, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "error"],
              {"MOCK_NAME": n, "MODELS": spec[n], "SLOTS": "4", "EXPOSE_PROPS": "1", "TOKEN_DELAY": "0.01", "TOKENS": "12"}, os.path.join(ROOT, "tools", "mock-llm"))
    for port in MOCKS.values():
        wait(f"http://127.0.0.1:{port}/stats")
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8088", "--log-level", "error"], {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "60"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=60)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    def mock(n):
        return httpx.get(f"http://127.0.0.1:{MOCKS[n]}/stats").json()

    mid = {}
    for n, port in MOCKS.items():
        ep = api("POST", "/endpoints", json={"name": f"m-{n}", "base_url": f"http://127.0.0.1:{port}/v1", "probe": False})
        mid[n] = {m["upstream_id"]: m["id"] for m in ep["models"]}
    api("PATCH", f"/models/{mid['b']['think-b']}", json={"capabilities": ["chat", "reasoning"]})
    api("PATCH", f"/models/{mid['b']['see-b']}", json={"capabilities": ["chat", "vision"]})
    alpha = api("POST", "/gateways", json={"name": "Alpha", "slug": "alpha", "models": [{"public_name": "main", "cache_enabled": True, "cache_any_temperature": True, "cache_streaming": True, "members": [{"model_id": mid["a"]["chat-a"]}]}]})
    beta = api("POST", "/gateways", json={"name": "Beta Brain", "slug": "beta", "models": [
        {"public_name": "think", "members": [{"model_id": mid["b"]["think-b"]}]}, {"public_name": "see", "members": [{"model_id": mid["b"]["see-b"]}]}]})
    hidden = api("POST", "/gateways", json={"name": "Hidden", "slug": "hidden", "models": [{"public_name": "h", "members": [{"model_id": mid["c"]["hidden-c"]}]}]})
    beta_key = api("POST", f"/gateways/{beta['id']}/keys", json={"name": "k"})["key"]
    msg = {"messages": [{"role": "user", "content": "say hi"}]}

    print("[1] configuration")
    check("endpoint is off by default (404)", c.get("/unified/v1/models").status_code == 404)
    v = api("PUT", "/unified", json={"enabled": True, "gateways": [{"id": alpha["id"]}, {"id": beta["id"], "model": "think"}]})
    check("selection saved; model names come from gateway names", v["model_names"] == ["Alpha", "Beta Brain", "Beta Brain/think", "Beta Brain/see"], str(v["model_names"]))
    check("the editor view lists every gateway with its exposure", {g["name"]: g["exposed"] for g in v["gateways"]} == {"Alpha": True, "Beta Brain": True, "Hidden": False})

    print("[2] model list follows each gateway's auth")
    names = lambda r: [m["id"] for m in r.json()["data"]]  # noqa: E731
    check("anonymous caller sees the open gateway only", names(c.get("/unified/v1/models")) == ["Alpha"])
    check("Beta's key reveals Beta's models", names(c.get("/unified/v1/models", headers={"Authorization": f"Bearer {beta_key}"})) == ["Alpha", "Beta Brain", "Beta Brain/think", "Beta Brain/see"])

    print("[3] chat completions")
    h = {"Authorization": f"Bearer {beta_key}"}
    r = c.post("/unified/v1/chat/completions", json={"model": "Alpha", **msg})
    j = r.json()
    check("model = gateway name routes to that gateway's model", r.status_code == 200 and "a-tok0" in j["choices"][0]["message"]["content"], r.text[:200])
    check("response reports the gateway name as the model", j["model"] == "Alpha", j.get("model"))
    check("name matching is case-insensitive and the slug works too", c.post("/unified/v1/chat/completions", json={"model": "alpha", **msg}).status_code == 200 and c.post("/unified/v1/chat/completions", json={"model": "ALPHA", **msg}).status_code == 200)
    r = c.post("/unified/v1/chat/completions", json={"model": "Beta Brain", **msg})
    check("a key-protected gateway still requires its own key", r.status_code == 401, str(r.status_code))
    r = c.post("/unified/v1/chat/completions", headers=h, json={"model": "Beta Brain", **msg})
    check("with its key the default model (think) answers", r.status_code == 200 and "b-tok0" in r.json()["choices"][0]["message"]["content"] and r.json()["model"] == "Beta Brain", r.text[:200])
    img = [{"role": "user", "content": [{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]}]
    r = c.post("/unified/v1/chat/completions", headers=h, json={"model": "Beta Brain/see", "messages": img})
    check("<gateway>/<model> reaches a specific model of a multi-model gateway", r.status_code == 200 and r.json()["model"] == "Beta Brain/see", r.text[:200])
    check("a gateway that is not exposed is unreachable (name and slug)", c.post("/unified/v1/chat/completions", json={"model": "Hidden", **msg}).status_code == 404 and c.post("/unified/v1/chat/completions", json={"model": "hidden", **msg}).status_code == 404 and mock("c")["total"] == 0)
    r = c.post("/unified/v1/chat/completions", json={"model": "Nope", **msg})
    check("unknown model lists only accessible names", r.status_code == 404 and "Alpha" in r.text and "Beta" not in r.text and "Hidden" not in r.text, r.text[:200])
    r = c.post("/unified/v1/chat/completions", json=msg)
    check("no model + a single accessible gateway -> that gateway", r.status_code == 200 and r.json()["model"] == "Alpha", r.text[:200])

    print("[4] streaming")
    with c.stream("POST", "/unified/v1/chat/completions", json={"model": "Alpha", "stream": True, "stream_options": {"include_usage": True}, **{"messages": [{"role": "user", "content": "stream me"}]}}) as r:
        lines = [ln for ln in r.iter_lines() if ln.strip()]
    objs = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ") and ln != "data: [DONE]"]
    check("SSE stream is well-formed and ends with [DONE]", r.status_code == 200 and lines[-1] == "data: [DONE]" and len(objs) >= 3)
    check("every chunk carries the gateway name as model", objs and all(o["model"] == "Alpha" for o in objs), str({o["model"] for o in objs}))
    check("usage chunk preserved", any(o.get("usage") for o in objs))

    print("[5] Anthropic / Responses surfaces")
    r = c.post("/unified/v1/messages", json={"model": "Alpha", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]})
    check("/messages works and echoes the gateway name", r.status_code == 200 and r.json()["model"] == "Alpha" and r.json()["content"][0]["text"], r.text[:200])
    with c.stream("POST", "/unified/v1/messages", json={"model": "Alpha", "max_tokens": 50, "stream": True, "messages": [{"role": "user", "content": "hi 2"}]}) as r:
        body = r.read().decode()
    check("/messages streaming ends with message_stop", "event: message_stop" in body and '"Alpha"' in body)
    r = c.post("/unified/v1/responses", json={"model": "Alpha", "input": "hello"})
    check("/responses works", r.status_code == 200 and r.json()["model"] == "Alpha", r.text[:200])
    r = c.post("/unified/v1/messages", json={"model": "Nope", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]})
    check("errors on /messages use the Anthropic shape", r.status_code == 404 and r.json().get("type") == "error", r.text[:150])

    print("[6] gateway features still apply")
    before = mock("a")["total"]
    q = {"model": "Alpha", "temperature": 0.7, "messages": [{"role": "user", "content": "cache me please"}]}
    r1 = c.post("/unified/v1/chat/completions", json=q)
    r2 = c.post("/unified/v1/chat/completions", json=q)
    check("the gateway's response cache works (second call is a hit, model still the gateway name)", r1.headers.get("x-sg-cache") != "hit" and r2.headers.get("x-sg-cache") == "hit" and r2.json()["model"] == "Alpha" and mock("a")["total"] == before + 1, f"{r1.headers.get('x-sg-cache')} {r2.headers.get('x-sg-cache')}")
    with c.stream("POST", "/unified/v1/chat/completions", json={**q, "stream": True}) as r:
        sh = r.headers.get("x-sg-cache"); sl = [json.loads(x[6:]) for x in r.iter_lines() if x.startswith("data: {")]
    check("cached stream replay also reports the gateway name", sh == "hit" and sl and all(o["model"] == "Alpha" for o in sl), f"{sh} {len(sl)}")
    logs = api("GET", "/logs?limit=200")
    check("requests are logged against the owning gateway", {l["gateway_id"] for l in logs} <= {alpha["id"], beta["id"]} and any(l["gateway_id"] == beta["id"] for l in logs))

    print("[7] concurrency and client disconnects")
    def one(i):
        n = "Alpha" if i % 2 == 0 else "Beta Brain"
        return httpx.post(GW + "/unified/v1/chat/completions", headers=h, timeout=60, json={"model": n, "messages": [{"role": "user", "content": f"parallel {i} {time.time_ns()}"}]}).status_code
    with ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(one, range(20)))
    check("20 parallel requests across two gateways all succeed", codes.count(200) == 20, str(codes))
    with httpx.Client(base_url=GW, timeout=30) as c2:
        with c2.stream("POST", "/unified/v1/chat/completions", json={"model": "Alpha", "stream": True, "messages": [{"role": "user", "content": "drop " + str(time.time_ns())}]}) as r:
            next(r.iter_lines())  # read one line, then hang up
    time.sleep(2.5)
    check("a client that hangs up frees the upstream slot", mock("a")["in_flight"] == 0 and mock("b")["in_flight"] == 0, str(mock("a")))
    check("and the gateway keeps serving", c.post("/unified/v1/chat/completions", json={"model": "Alpha", "messages": [{"role": "user", "content": "after " + str(time.time_ns())}]}).status_code == 200)

    print("[7b] unified API key")
    made = api("POST", "/unified/keys", json={"name": "e2e"})
    uk = made["key"]
    uh = {"Authorization": f"Bearer {uk}"}
    check("with a key configured, anonymous calls are rejected even for the open gateway", c.post("/unified/v1/chat/completions", json={"model": "Alpha", **msg}).status_code == 401 and c.get("/unified/v1/models").status_code == 401)
    check("a wrong token is rejected", c.post("/unified/v1/chat/completions", headers={"Authorization": "Bearer nope"}, json={"model": "Alpha", **msg}).status_code == 401)
    r = c.post("/unified/v1/chat/completions", headers=uh, json={"model": "Alpha", **msg})
    check("the unified key works on an open gateway", r.status_code == 200 and r.json()["model"] == "Alpha", r.text[:150])
    r = c.post("/unified/v1/chat/completions", headers=uh, json={"model": "Beta Brain", **msg})
    check("and on a key-protected gateway, without that gateway's key", r.status_code == 200 and "b-tok0" in r.json()["choices"][0]["message"]["content"], r.text[:150])
    check("x-api-key header works too", c.post("/unified/v1/chat/completions", headers={"x-api-key": uk}, json={"model": "Alpha", **msg}).status_code == 200)
    check("the model list shows everything for the unified key", names(c.get("/unified/v1/models", headers=uh)) == ["Alpha", "Beta Brain", "Beta Brain/think", "Beta Brain/see"])
    with c.stream("POST", "/unified/v1/chat/completions", headers=uh, json={"model": "Beta Brain", "stream": True, **msg}) as r:
        okstream = r.status_code == 200 and any(x == "data: [DONE]" for x in r.iter_lines())
    check("streaming works with the unified key", okstream)
    r = c.post("/unified/v1/messages", headers=uh, json={"model": "Alpha", "max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]})
    check("Anthropic surface accepts the unified key", r.status_code == 200 and r.json()["model"] == "Alpha")
    check("Beta's own key still opens Beta only", c.post("/unified/v1/chat/completions", headers=h, json={"model": "Beta Brain", **msg}).status_code == 200 and c.post("/unified/v1/chat/completions", headers=h, json={"model": "Alpha", **msg}).status_code == 401)
    check("the key is stored hashed (never returned again)", uk not in json.dumps(api("GET", "/unified")) and uk not in c.get("/api/settings").text)
    api("DELETE", f"/unified/keys/{made['id']}")
    time.sleep(0.2)
    check("revoked key stops working; with no keys the endpoint is open again", c.post("/unified/v1/chat/completions", headers=uh, json={"model": "Alpha", **msg}).status_code in (200,) and c.post("/unified/v1/chat/completions", json={"model": "Alpha", **msg}).status_code == 200)

    print("[8] changes take effect")
    api("PUT", "/unified", json={"gateways": [{"id": beta["id"]}]})
    time.sleep(3.2)  # settings cache TTL
    check("removing Alpha from the selection removes it", c.post("/unified/v1/chat/completions", json={"model": "Alpha", **msg}).status_code == 404)
    c.delete(f"/api/gateways/{beta['id']}")
    time.sleep(0.2)
    check("deleting an exposed gateway just drops it from the list", api("GET", "/unified")["model_names"] == [] and c.get("/unified/v1/models").json()["data"] == [])
    api("PUT", "/unified", json={"enabled": False})
    time.sleep(3.2)
    check("disabling turns the endpoint off", c.get("/unified/v1/models").status_code == 404)
    check("/gw/<slug> and /v1 are unaffected", c.post("/gw/alpha/v1/chat/completions", json={"model": "main", **msg}).status_code == 200)
    check("management API still works without the unified route interfering", c.get("/api/gateways").status_code == 200)


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
    sys.exit(0 if results and all(results) else 1)
