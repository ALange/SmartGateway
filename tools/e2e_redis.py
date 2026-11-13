#!/usr/bin/env python3
"""End-to-end checks for the response cache and the optional Redis backend.

Needs Docker (starts temporary redis:7-alpine containers) and a Python with the backend requirements:
    python tools/e2e_redis.py
Uses throw-away mock upstreams and its own data dir; never touches real endpoints or your data.
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
G1, G2 = "http://127.0.0.1:8094", "http://127.0.0.1:8093"
R1, R2 = "e2e-redis", "e2e-redis-auth"


def sh(*cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def rcli(container, *args, pw=None):
    cmd = ["docker", "exec", container, "redis-cli"] + (["-a", pw, "--no-auth-warning"] if pw else []) + list(args)
    return sh(*cmd).stdout.strip()


def spawn(cmd, env, cwd):
    p = subprocess.Popen(cmd, env={**os.environ, **env}, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    return p


def wait(url, tries=60):
    for _ in range(tries):
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise RuntimeError(f"not up: {url}")


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def start_gateway(port, data):
    p = spawn([PY, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "error"],
              {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "30", "SG_CACHE_RETRY_AFTER": "2"}, os.path.join(ROOT, "backend"))
    wait(f"http://127.0.0.1:{port}/healthz")
    return p


def main():
    sh("docker", "rm", "-f", R1, R2)
    assert sh("docker", "run", "-d", "--name", R1, "-p", "6390:6379", "redis:7-alpine").returncode == 0, "cannot start redis (docker pull?)"
    assert sh("docker", "run", "-d", "--name", R2, "-p", "6391:6379", "redis:7-alpine", "redis-server", "--requirepass", "s3cret").returncode == 0
    for c, pw in ((R1, None), (R2, "s3cret")):
        for _ in range(40):
            if rcli(c, "ping", pw=pw) == "PONG":
                break
            time.sleep(0.25)
    data = tempfile.mkdtemp(prefix="sgredis-")
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9201", "--log-level", "error"], {"MOCK_NAME": "m", "MODELS": "alpha", "SLOTS": "4", "TOKEN_DELAY": "0.01"}, os.path.join(ROOT, "tools", "mock-llm"))
    wait("http://127.0.0.1:9201/stats")
    g1 = start_gateway(8094, data)
    c = httpx.Client(base_url=G1, timeout=30)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    ep = api("POST", "/endpoints", json={"name": "m", "base_url": "http://127.0.0.1:9201/v1", "probe": False})
    mid = ep["models"][0]["id"]
    api("POST", "/gateways", json={"name": "c", "slug": "c", "models": [
        {"public_name": "cached", "cache_enabled": True, "cache_ttl": 5, "members": [{"model_id": mid}]},
        {"public_name": "plain", "members": [{"model_id": mid}]},
        {"public_name": "anytemp", "cache_enabled": True, "cache_any_temperature": True, "members": [{"model_id": mid}]},
        {"public_name": "streamy", "cache_enabled": True, "cache_streaming": True, "members": [{"model_id": mid}]},
    ]})

    def upstream_total():
        return httpx.get("http://127.0.0.1:9201/stats").json()["total"]

    def ask(model, text="hello", temp=0, headers=None, client=None, **extra):
        body = {"model": model, "max_tokens": 3, "messages": [{"role": "user", "content": text}], **extra}
        if temp is not None:
            body["temperature"] = temp
        return (client or c).post("/gw/c/v1/chat/completions", json=body, headers=headers or {})

    print("[1] Redis off -> in-memory cache, per-model switch")
    t0 = upstream_total()
    r1, r2 = ask("cached", "q1"), ask("cached", "q1")
    check("memory: first miss, second hit", r1.headers.get("x-sg-cache") == "miss" and r2.headers.get("x-sg-cache") == "hit" and upstream_total() - t0 == 1, f"{r1.headers.get('x-sg-cache')} {r2.headers.get('x-sg-cache')}")
    check("hit returns identical body", r1.json()["choices"] == r2.json()["choices"])
    t0 = upstream_total(); ask("plain", "q1"); ask("plain", "q1")
    check("model with caching off never caches", upstream_total() - t0 == 2 and "x-sg-cache" not in ask("plain", "q1").headers)
    check("temperature 0.7 skipped on cached model", ask("cached", "q2", temp=0.7).headers.get("x-sg-cache") == "skip")
    check("cache_any_temperature caches temp 0.7", [ask("anytemp", "q3", temp=0.7).headers.get("x-sg-cache"), ask("anytemp", "q3", temp=0.7).headers.get("x-sg-cache")] == ["miss", "hit"])
    check("X-SG-Cache: 0 bypasses", ask("cached", "q1", headers={"X-SG-Cache": "0"}).headers.get("x-sg-cache") == "bypass")
    check("X-SG-Cache: 1 forces caching of a non-deterministic request", [ask("cached", "q4", temp=0.9, headers={"X-SG-Cache": "1"}).headers.get("x-sg-cache"), ask("cached", "q4", temp=0.9, headers={"X-SG-Cache": "1"}).headers.get("x-sg-cache")] == ["miss", "hit"])
    st = api("GET", "/cache/stats")
    check("stats report memory backend", st["backend"] == "memory" and st["hits"] >= 3 and st["entries"] >= 1, str(st))
    check("hits logged with cached flag", api("GET", "/cache/stats")["hits_24h"] >= 3)
    check("streaming requests are not cached", "x-sg-cache" not in ask("cached", "q1", stream=True).headers)

    print("[2] connection test")
    t = api("POST", "/settings/redis/test", json={"url": "redis://127.0.0.1:6390/0"})
    check("test connection ok (version + latency)", t["ok"] and t["version"].startswith("7") and t["latency_ms"] >= 0, str(t))
    t = api("POST", "/settings/redis/test", json={"url": "redis://127.0.0.1:1/0"})
    check("unreachable host -> clear error", t["ok"] is False and t["error"], str(t))
    t = api("POST", "/settings/redis/test", json={"url": "redis://:wrong@127.0.0.1:6391/0"})
    check("wrong password -> error, password not echoed", t["ok"] is False and "wrong" not in json.dumps(t).replace("wrong password", ""), str(t))
    check("password-protected redis accepts the right password", api("POST", "/settings/redis/test", json={"url": "redis://:s3cret@127.0.0.1:6391/0"})["ok"])

    print("[3] switch to Redis")
    api("PUT", "/settings", json={"redis_url": "redis://127.0.0.1:6390/0", "redis_enabled": True, "redis_key_prefix": "e2e:"})
    check("backend is now redis/connected", api("GET", "/cache/stats")["backend"] == "redis" and api("GET", "/cache/stats")["state"] == "connected")
    t0 = upstream_total()
    a, b = ask("cached", "r1"), ask("cached", "r1")
    check("redis: miss then hit", a.headers.get("x-sg-cache") == "miss" and b.headers.get("x-sg-cache") == "hit" and upstream_total() - t0 == 1)
    keys = rcli(R1, "keys", "e2e:cache:*").split()
    check("entry stored in Redis under the prefix", len(keys) == 1 and keys[0].startswith("e2e:cache:"), str(keys))
    ttl = int(rcli(R1, "ttl", keys[0]))
    check("per-model TTL (5 s) applied", 0 < ttl <= 5, str(ttl))
    time.sleep(6)
    t0 = upstream_total()
    check("entry expires after TTL -> miss again", ask("cached", "r1").headers.get("x-sg-cache") == "miss" and upstream_total() - t0 == 1)

    print("[4] persistence + sharing")
    ask("anytemp", "p1")
    g1.terminate(); g1.wait(); time.sleep(0.5)
    g1 = start_gateway(8094, data)
    t0 = upstream_total()
    r = ask("anytemp", "p1")
    check("after gateway restart the cache survives", r.headers.get("x-sg-cache") == "hit" and upstream_total() - t0 == 0, r.headers.get("x-sg-cache", ""))
    check("settings persisted across restart (redis still active)", api("GET", "/cache/stats")["backend"] == "redis")
    start_gateway(8093, data)
    c2 = httpx.Client(base_url=G2, timeout=30)
    ask("anytemp", "shared-1")
    check("second instance sharing Redis hits the first one's entry", ask("anytemp", "shared-1", client=c2).headers.get("x-sg-cache") == "hit")

    print("[4b] streaming entries")

    def sstream(client, text):
        with client.stream("POST", "/gw/c/v1/chat/completions", json={"model": "streamy", "temperature": 0, "stream": True, "messages": [{"role": "user", "content": text}]}) as r:
            hdr = r.headers
            body_ = "".join(ln for ln in r.iter_lines())
        return hdr, body_

    t0 = upstream_total()
    h1, b1 = sstream(c, "stream persist")
    time.sleep(0.6)
    keys_now = rcli(R1, "keys", "e2e:cache:*").split()
    h2, b2 = sstream(c2, "stream persist")
    check("a stream stored through instance 1 is replayed by instance 2 via Redis", h1.get("x-sg-cache") == "miss" and h2.get("x-sg-cache") == "hit" and upstream_total() - t0 == 1, f"{h1.get('x-sg-cache')} {h2.get('x-sg-cache')}")
    stored = [k for k in keys_now if '"origin":"stream"' in rcli(R1, "get", k)]
    check("the entry in Redis is the aggregated response (origin=stream), not a chunk log", len(stored) >= 1 and all('"chat.completion"' in rcli(R1, "get", k) and "chat.completion.chunk" not in rcli(R1, "get", k) for k in stored))
    jr = c2.post("/gw/c/v1/chat/completions", json={"model": "streamy", "temperature": 0, "messages": [{"role": "user", "content": "stream persist"}]})
    check("the same entry serves a non-streaming client on the other instance", jr.headers.get("x-sg-cache") == "hit" and jr.json()["choices"][0]["message"]["content"] and "_sg" not in jr.json())

    print("[5] Redis outage")
    ask("anytemp", "o1")
    sh("docker", "stop", R1)
    codes = [ask("anytemp", f"down-{i}").status_code for i in range(3)]
    check("requests keep succeeding while Redis is down", codes == [200, 200, 200], str(codes))
    st = api("GET", "/cache/stats")
    check("status shows error with a message", st["state"] == "error" and st["last_error"], str(st))
    t0 = upstream_total(); ask("anytemp", "mem-fb"); r = ask("anytemp", "mem-fb")
    check("memory fallback still caches during the outage", r.headers.get("x-sg-cache") == "hit" and upstream_total() - t0 == 1)
    sh("docker", "start", R1)
    for _ in range(40):
        if rcli(R1, "ping") == "PONG":
            break
        time.sleep(0.25)
    time.sleep(2.5)  # retry window
    ask("anytemp", "back-1")
    check("auto-recovers when Redis returns (no restart)", api("GET", "/cache/stats")["state"] == "connected")
    check("new entries land in Redis again", len(rcli(R1, "keys", "e2e:cache:*").split()) >= 1)

    print("[6] fallback disabled")
    api("PUT", "/settings", json={"cache_fallback_memory": False})
    sh("docker", "stop", R1)
    t0 = upstream_total(); ask("anytemp", "nofb"); ask("anytemp", "nofb")
    check("without fallback nothing is cached during outage (but requests still work)", upstream_total() - t0 == 2)
    sh("docker", "start", R1)
    for _ in range(40):
        if rcli(R1, "ping") == "PONG":
            break
        time.sleep(0.25)
    api("PUT", "/settings", json={"cache_fallback_memory": True})

    print("[7] clear + prefix safety")
    rcli(R1, "set", "other:keep", "1")
    time.sleep(2.5)
    ask("anytemp", "c1")
    n_before = len(rcli(R1, "keys", "e2e:cache:*").split())
    r = api("POST", "/cache/clear")
    check("clear removes our entries only", n_before >= 1 and rcli(R1, "keys", "e2e:cache:*") == "" and rcli(R1, "get", "other:keep") == "1", str(r))

    print("[8] password-protected Redis + secret handling")
    t = api("POST", "/settings/redis/test", json={"url": "redis://127.0.0.1:6391/0", "username": "", "password": "s3cret"})
    check("test connection with separate password field", t["ok"], str(t))
    api("PUT", "/settings", json={"redis_url": "redis://127.0.0.1:6391/0", "redis_username": "", "redis_password": "s3cret", "redis_enabled": True})
    ask("anytemp", "auth-1")
    check("works with a password-protected Redis", api("GET", "/cache/stats")["state"] == "connected" and len(rcli(R2, "keys", "e2e:cache:*", pw="s3cret").split()) >= 1)
    s = api("GET", "/settings")
    check("GET /settings shows URL and username in clear, never the password", "s3cret" not in json.dumps(s) and s["redis_url"] == "redis://127.0.0.1:6391/0" and s["has_password"] is True, str(s))
    con = sqlite3.connect(os.path.join(data, "smartgateway.db"))
    raw_pw = con.execute("select value from setting where key='redis_password'").fetchone()[0]
    raw_url = con.execute("select value from setting where key='redis_url'").fetchone()[0]
    check("password stored encrypted, URL stored plain", "s3cret" not in raw_pw and json.loads(raw_pw).startswith("enc:") and "s3cret" not in raw_url and not json.loads(raw_url).startswith("enc:"))
    api("PUT", "/settings", json={"redis_url": "redis://:s3cret@127.0.0.1:6391/0"})
    s2 = api("GET", "/settings")
    check("credentials pasted inside the URL are split into fields", s2["redis_url"] == "redis://127.0.0.1:6391/0" and s2["has_password"] and api("GET", "/cache/stats")["state"] == "connected", str(s2))
    api("PUT", "/settings", json={"redis_url": "", "redis_enabled": False})
    st = api("GET", "/cache/stats")
    check("clearing the URL returns to in-memory backend and drops the password", st["backend"] == "memory" and not api("GET", "/settings")["has_url"] and not api("GET", "/settings")["has_password"])
    check("validation rejects enabling Redis without URL", c.put("/api/settings", json={"redis_enabled": True}).status_code == 400)


try:
    main()
except Exception:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    results.append(False)
finally:
    for p in procs:
        p.terminate()
    sh("docker", "rm", "-f", R1, R2)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if results and all(results) else 1)
