#!/usr/bin/env python3
"""End-to-end checks for OpenCode Zen / Go endpoints, against mock upstreams that imitate their behaviour
(OpenCode-style /models metadata, API key, and Go's mandatory x-opencode-session header). Nothing real is contacted."""
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
procs, results = [], []
GW = "http://127.0.0.1:8089"
Z, G, P = "http://127.0.0.1:9531", "http://127.0.0.1:9532", "http://127.0.0.1:9533"


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
    data = tempfile.mkdtemp(prefix="sgopencode-")
    mock = os.path.join(ROOT, "tools", "mock-llm")
    base = {"EXPOSE_PROPS": "1", "TOKEN_DELAY": "0.01", "TOKENS": "10", "SLOTS": "4"}
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9531", "--log-level", "error"], {**base, "MOCK_NAME": "zen", "OPENCODE_MODE": "zen", "MOCK_KEY": "zen-key", "CTX": "100000",
          "MODELS": "z-chat:tools,z-think:reasoning+tools,z-see:vision+tools,z-open-free"}, mock)
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9532", "--log-level", "error"], {**base, "MOCK_NAME": "go", "OPENCODE_MODE": "go", "MOCK_KEY": "go-key", "MODELS": "g-chat:tools,g-think:reasoning+tools,g-beam-free"}, mock)
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9533", "--log-level", "error"], {**base, "MOCK_NAME": "plain", "MODELS": "plain-m"}, mock)
    for u in (Z, G, P):
        wait(u + "/stats")
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8089", "--log-level", "error"], {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "60"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=60)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    hits = lambda u: httpx.get(u + "/hits").json()  # noqa: E731
    probe_paths = ("/props", "/slots", "/metrics", "/api/show", "/v1/embeddings")

    print("[1] adding OpenCode Zen")
    r = c.post("/api/endpoints", json={"name": "zen", "provider": "opencode-zen", "base_url": Z + "/v1", "api_key": "wrong-key"})
    check("a wrong API key is rejected up front (catalog is public, so this needs the key check)", r.status_code == 400 and "rejected" in r.text, r.text[:150])
    check("nothing is left behind after the rejection", api("GET", "/endpoints") == [])
    zen = api("POST", "/endpoints", json={"name": "zen", "provider": "opencode-zen", "base_url": Z + "/v1", "api_key": "zen-key"})
    zh = hits(Z)
    check("provider recorded, no local-engine slot detection", zen["provider"] == "opencode-zen" and zen["slot_source"] == "none", str(zen["provider"]))
    check("no probe traffic at all: only the free-model key check hit /chat/completions", zh.get("/v1/chat/completions") == 2 and not any(zh.get(p) for p in probe_paths), str(zh))
    by = {m["upstream_id"]: m for m in zen["models"]}
    check("capabilities come from the metadata", "reasoning" in by["z-think"]["capabilities"] and "vision" in by["z-see"]["capabilities"] and "vision" not in by["z-chat"]["capabilities"] and "tools" in by["z-chat"]["capabilities"], str({k: v["capabilities"] for k, v in by.items()}))
    check("context size comes from limit.context", all(m["context_length"] == 100000 for m in zen["models"]), str([m["context_length"] for m in zen["models"]]))
    check("hosted models default to 8 concurrent slots", all(m["max_slots"] == 8 for m in zen["models"]))
    check("the API key is not exposed", "zen-key" not in json.dumps(zen) and zen["api_key"] is True)

    print("[2] adding OpenCode Go (requires x-opencode-session)")
    check("the mock really refuses a Go request without the header", httpx.post(G + "/v1/chat/completions", headers={"Authorization": "Bearer go-key"}, json={"model": "g-chat", "messages": []}).status_code == 400)
    go = api("POST", "/endpoints", json={"name": "go", "provider": "opencode-go", "base_url": G + "/v1", "api_key": "go-key"})
    gh = hits(G)
    lh = httpx.get(G + "/last_headers").json()
    check("Go endpoint added; the key check carried the session header and a client user agent", go["provider"] == "opencode-go" and lh.get("x-opencode-session") and lh.get("user-agent", "").startswith("SmartGateway/"), str(lh))
    check("no probes on Go either", not any(gh.get(p) for p in probe_paths), str(gh))
    rs = api("POST", f"/endpoints/{go['id']}/rescan?probe=true")
    check("re-scan keeps the provider and still sends no probes", rs["provider"] == "opencode-go" and not any(hits(G).get(p) for p in probe_paths) and hits(G).get("/v1/chat/completions") == gh.get("/v1/chat/completions"))
    gmid = {m["upstream_id"]: m["id"] for m in go["models"]}
    zmid = {m["upstream_id"]: m["id"] for m in zen["models"]}

    print("[3] requests through the gateway")
    gw = api("POST", "/gateways", json={"name": "Go GW", "slug": "gogw", "models": [
        {"public_name": "code", "members": [{"model_id": gmid["g-chat"]}]}, {"public_name": "think", "members": [{"model_id": gmid["g-think"]}]}]})
    zgw = api("POST", "/gateways", json={"name": "Zen GW", "slug": "zengw", "models": [{"public_name": "z", "members": [{"model_id": zmid["z-chat"]}]}]})

    def ask(slug, model, msgs, **kw):
        return c.post(f"/gw/{slug}/v1/chat/completions", json={"model": model, "messages": msgs, **kw})
    convo = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "How do I sort a list?"}]
    r1 = ask("gogw", "code", convo)
    h1 = httpx.get(G + "/last_headers").json()
    check("a Go request succeeds because the session header is added", r1.status_code == 200 and h1.get("x-opencode-session", "").startswith("sg-"), r1.text[:150])
    check("the upstream key is forwarded and the user agent identifies the gateway", h1.get("authorization") == "Bearer go-key" and h1.get("user-agent", "").startswith("SmartGateway/"))
    r2 = ask("gogw", "code", convo + [{"role": "assistant", "content": "use sorted()"}, {"role": "user", "content": "and reverse?"}])
    h2 = httpx.get(G + "/last_headers").json()
    check("the same conversation keeps the same session id", r2.status_code == 200 and h2["x-opencode-session"] == h1["x-opencode-session"])
    ask("gogw", "code", [{"role": "user", "content": "A completely different topic"}])
    h3 = httpx.get(G + "/last_headers").json()
    check("a different conversation gets a different session id", h3["x-opencode-session"] != h1["x-opencode-session"])
    ask("gogw", "code", convo, user="explicit-user")
    ask_h = httpx.post(GW + "/gw/gogw/v1/chat/completions", headers={"x-sg-session": "my-session"}, json={"model": "code", "messages": convo})
    h4 = httpx.get(G + "/last_headers").json()
    ask_h2 = httpx.post(GW + "/gw/gogw/v1/chat/completions", headers={"x-sg-session": "my-session"}, json={"model": "code", "messages": [{"role": "user", "content": "other text"}]})
    h5 = httpx.get(G + "/last_headers").json()
    check("an explicit X-SG-Session is honoured", ask_h.status_code == 200 and ask_h2.status_code == 200 and h4["x-opencode-session"] == h5["x-opencode-session"] != h1["x-opencode-session"])
    with c.stream("POST", "/gw/gogw/v1/chat/completions", json={"model": "think", "stream": True, "messages": convo}) as r:
        lines = [x for x in r.iter_lines() if x.strip()]
    check("streaming works (and the reasoning model answers)", r.status_code == 200 and lines[-1] == "data: [DONE]" and len(lines) > 3)
    rz = ask("zengw", "z", convo)
    hz = httpx.get(Z + "/last_headers").json()
    check("Zen requests also carry a session id and the user agent", rz.status_code == 200 and hz.get("x-opencode-session") and hz.get("user-agent", "").startswith("SmartGateway/"))

    print("[4] other features keep working")
    api("PUT", "/unified", json={"enabled": True, "gateways": [{"id": gw["id"]}]})
    time.sleep(0.3)
    ru = c.post("/unified/v1/chat/completions", json={"model": "Go GW", "messages": convo})
    check("a gateway backed by Go can be exposed on the unified endpoint", ru.status_code == 200 and ru.json()["model"] == "Go GW", ru.text[:150])
    plain = api("POST", "/endpoints", json={"name": "plain", "base_url": P + "/v1"})
    ph = hits(P)
    check("a regular endpoint is still probed the old way and has no provider", plain["provider"] is None and ph.get("/props", 0) >= 1 and plain["slot_source"] != "none", str(ph))
    pgw = api("POST", "/gateways", json={"name": "Plain GW", "slug": "plaingw", "models": [{"public_name": "p", "members": [{"model_id": plain["models"][0]["id"]}]}]})
    ask("plaingw", "p", convo)
    check("regular endpoints never receive OpenCode headers", "x-opencode-session" not in httpx.get(P + "/last_headers").json())
    pol = api("GET", "/endpoints")
    check("endpoint list shows each provider", {e["name"]: e["provider"] for e in pol} == {"zen": "opencode-zen", "go": "opencode-go", "plain": None})


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
