#!/usr/bin/env python3
"""End-to-end checks for streaming response caching (stream miss -> stored, stream hit -> replayed, cross-mode sharing...).

Spawns a mock upstream + a throw-away gateway (own data dir). Never touches real endpoints or your data.
    python tools/e2e_stream_cache.py
"""
import concurrent.futures as cf
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
GW = "http://127.0.0.1:8092"


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
    data = tempfile.mkdtemp(prefix="sgsc-")
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9401", "--log-level", "error"],
          {"MOCK_NAME": "m", "MODELS": "plain-m,tool-m:tools,think-m:reasoning", "SLOTS": "8", "TOKEN_DELAY": "0.05", "TOKENS": "30"}, os.path.join(ROOT, "tools", "mock-llm"))
    wait("http://127.0.0.1:9401/stats")
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8092", "--log-level", "error"], {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "30"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=60)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    models = {m["upstream_id"]: m["id"] for m in api("POST", "/endpoints", json={"name": "m", "base_url": "http://127.0.0.1:9401/v1", "probe": False})["models"]}
    cached = {"cache_enabled": True, "cache_streaming": True}
    api("POST", "/gateways", json={"name": "c", "slug": "c", "models": [
        {"public_name": "s-on", **cached, "members": [{"model_id": models["plain-m"]}]},
        {"public_name": "s-off", "cache_enabled": True, "cache_streaming": False, "members": [{"model_id": models["plain-m"]}]},
        {"public_name": "s-real", **cached, "cache_replay_speed": "realistic", "members": [{"model_id": models["plain-m"]}]},
        {"public_name": "s-tool", **cached, "members": [{"model_id": models["tool-m"]}]},
        {"public_name": "s-think", **cached, "members": [{"model_id": models["think-m"]}]},
    ]})
    total = lambda: httpx.get("http://127.0.0.1:9401/stats").json()["total"]  # noqa: E731

    def body(model, text, stream, **extra):
        b = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": text}], **extra}
        if stream:
            b["stream"] = True
        return b

    def stream(model, text, headers=None, path="/gw/c/v1/chat/completions", **extra):
        """-> dict(status, headers, lines, objs, text, reasoning, tools, finish, usage, seconds, ttft)."""
        t0, ttft, lines = time.time(), None, []
        with c.stream("POST", path, json=body(model, text, True, **extra), headers=headers or {}) as r:
            hdr, status = r.headers, r.status_code
            for ln in r.iter_lines():
                if ttft is None and ln.startswith("data:") and "content" in ln and '"content":""' not in ln:
                    ttft = time.time() - t0
                lines.append(ln)
        objs = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ") and ln != "data: [DONE]"]
        deltas = [o["choices"][0]["delta"] for o in objs if o.get("choices")]
        tools = {}
        for d in deltas:
            for t in d.get("tool_calls") or []:
                e = tools.setdefault(t["index"], {"name": "", "args": ""})
                e["name"] = e["name"] or (t.get("function") or {}).get("name", "")
                e["args"] += (t.get("function") or {}).get("arguments", "")
        return {"status": status, "headers": hdr, "lines": lines, "objs": objs, "text": "".join(d.get("content") or "" for d in deltas),
                "reasoning": "".join(d.get("reasoning_content") or "" for d in deltas), "tools": tools,
                "finish": next((o["choices"][0]["finish_reason"] for o in objs if o.get("choices") and o["choices"][0].get("finish_reason")), None),
                "usage": next((o["usage"] for o in objs if o.get("usage")), None), "seconds": time.time() - t0, "ttft": ttft}

    def plain(model, text, **extra):
        return c.post("/gw/c/v1/chat/completions", json=body(model, text, False, **extra))

    settle = lambda: time.sleep(0.5)  # noqa: E731  (the cache write happens in the background after the stream ends)

    print("[1] stream miss -> stream hit")
    t0 = total()
    a = stream("s-on", "tell me a story")
    settle()
    b = stream("s-on", "tell me a story")
    check("first stream is a live miss with a full answer", a["status"] == 200 and a["headers"].get("x-sg-cache") == "miss" and len(a["text"]) > 50 and a["finish"] == "stop", a["headers"].get("x-sg-cache", ""))
    check("second stream is served from cache without calling the upstream", b["headers"].get("x-sg-cache") == "hit" and total() - t0 == 1, f"{b['headers'].get('x-sg-cache')} {total() - t0}")
    check("replayed text, finish reason and usage are identical", b["text"] == a["text"] and b["finish"] == a["finish"] and b["usage"] == a["usage"] and b["usage"]["total_tokens"] > 0)
    check("replay is much faster than the live stream", b["seconds"] < a["seconds"] * 0.3, f"{b['seconds']:.2f}s vs {a['seconds']:.2f}s")
    check("SSE framing: role chunk first, finish chunk, usage chunk, [DONE] last",
          b["objs"][0]["choices"][0]["delta"].get("role") == "assistant" and b["lines"][-2:] == ["data: [DONE]", ""] and b["objs"][-1].get("choices") == [] and b["usage"] and
          all(ln == "" or ln.startswith("data: ") for ln in b["lines"]))
    check("chunk objects look like OpenAI chunks with the public model name", all(o["object"] == "chat.completion.chunk" and o["model"] == "s-on" for o in b["objs"]))
    check("replay header present", b["headers"].get("x-sg-replay") == "instant")

    print("[2] cross-mode sharing")
    t0 = total()
    j = plain("s-on", "tell me a story")
    check("entry written by a stream serves a non-streaming client", j.status_code == 200 and j.headers.get("x-sg-cache") == "hit" and total() == t0
          and j.json()["choices"][0]["message"]["content"] == a["text"] and j.json()["object"] == "chat.completion" and "_sg" not in j.json(), j.text[:150])
    j1 = plain("s-on", "a different question")
    s2 = stream("s-on", "a different question")
    check("entry written by a non-streaming request serves a streaming client", j1.headers.get("x-sg-cache") == "miss" and s2["headers"].get("x-sg-cache") == "hit"
          and s2["text"] == j1.json()["choices"][0]["message"]["content"], f"{s2['headers'].get('x-sg-cache')}")

    print("[3] incomplete streams are never stored")
    with c.stream("POST", "/gw/c/v1/chat/completions", json=body("s-on", "client walks away", True)) as r:
        n = 0
        for ln in r.iter_lines():
            n += 1
            if n >= 4:
                break  # close the connection mid-stream
    time.sleep(1.5)
    t0 = total()
    x = stream("s-on", "client walks away")
    check("after a client disconnect the next identical request is still a miss", x["headers"].get("x-sg-cache") == "miss" and total() - t0 == 1, x["headers"].get("x-sg-cache", ""))
    httpx.post("http://127.0.0.1:9401/admin/stream_abort/6")
    try:
        stream("s-on", "upstream breaks")
    except Exception:  # noqa: BLE001
        pass
    httpx.post("http://127.0.0.1:9401/admin/stream_abort/0")
    settle()
    y = stream("s-on", "upstream breaks")
    check("a stream that broke mid-way (upstream error) is not stored", y["headers"].get("x-sg-cache") == "miss" and y["finish"] == "stop", y["headers"].get("x-sg-cache", ""))

    print("[4] tool calls and reasoning survive the replay")
    ta = stream("s-tool", "weather?", tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}])
    settle()
    tb = stream("s-tool", "weather?", tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}])
    check("tool-call stream: live and replay assemble to the same call", ta["tools"] and tb["headers"].get("x-sg-cache") == "hit" and tb["tools"] == ta["tools"] and tb["finish"] == "tool_calls"
          and json.loads(tb["tools"][0]["args"]) == {"city": "Paris", "unit": "celsius"}, f"{ta['tools']} {tb['tools']}")
    ra = stream("s-think", "why is the sky blue?")
    settle()
    rb = stream("s-think", "why is the sky blue?")
    check("reasoning content is preserved on replay", ra["reasoning"] == "Let me think..." and rb["headers"].get("x-sg-cache") == "hit" and rb["reasoning"] == ra["reasoning"] and rb["text"] == ra["text"])

    print("[5] replay speed")
    live = stream("s-real", "pace me")
    settle()
    inst = stream("s-on", "pace me")  # different public model -> its own live miss first
    settle()
    inst = stream("s-on", "pace me")
    real = stream("s-real", "pace me")
    check("realistic replay takes about as long as the original", real["headers"].get("x-sg-cache") == "hit" and real["headers"].get("x-sg-replay") == "realistic"
          and 0.5 * live["seconds"] < real["seconds"] < 1.7 * live["seconds"], f"live {live['seconds']:.2f}s replay {real['seconds']:.2f}s")
    check("instant replay is far quicker than realistic", inst["seconds"] < 0.4 * real["seconds"], f"{inst['seconds']:.2f}s vs {real['seconds']:.2f}s")
    check("both replays carry the same text", real["text"] == live["text"])

    print("[6] opt-in and exclusions")
    t0 = total()
    o1, o2 = stream("s-off", "not opted in"), stream("s-off", "not opted in")
    check("model without 'cache streaming': streams untouched (no header, upstream called twice)", "x-sg-cache" not in o1["headers"] and "x-sg-cache" not in o2["headers"] and total() - t0 == 2)
    p1, p2 = plain("s-off", "but json works"), plain("s-off", "but json works")
    check("…while non-streaming caching on the same model keeps working", p1.headers.get("x-sg-cache") == "miss" and p2.headers.get("x-sg-cache") == "hit")
    t0 = total()
    l1, l2 = stream("s-on", "with logprobs", logprobs=True), stream("s-on", "with logprobs", logprobs=True)
    check("logprobs requests are never cached", "x-sg-cache" not in l1["headers"] and total() - t0 == 2)
    t0 = total()
    n1, n2 = stream("s-on", "two choices", n=2), stream("s-on", "two choices", n=2)
    check("n > 1 requests are never cached", "x-sg-cache" not in n1["headers"] and total() - t0 == 2)
    bp = stream("s-on", "tell me a story", headers={"X-SG-Cache": "0"})
    check("X-SG-Cache: 0 bypasses an existing entry", bp["headers"].get("x-sg-cache") == "bypass" and bp["text"] != "" and bp["headers"].get("x-sg-replay") is None)
    hot = stream("s-on", "warm one", temperature=0.9)
    check("non-deterministic stream is skipped by default", hot["headers"].get("x-sg-cache") == "skip")
    fm1 = stream("s-on", "forced", headers={"X-SG-Cache": "1"}, temperature=0.9)
    settle()
    fm2 = stream("s-on", "forced", headers={"X-SG-Cache": "1"}, temperature=0.9)
    check("X-SG-Cache: 1 forces caching of a non-deterministic stream", fm1["headers"].get("x-sg-cache") == "miss" and fm2["headers"].get("x-sg-cache") == "hit")

    print("[7] other client APIs")
    anth = {"model": "s-on", "max_tokens": 64, "temperature": 0, "stream": True, "messages": [{"role": "user", "content": "anthropic stream"}]}

    def anth_stream():
        with c.stream("POST", "/gw/c/v1/messages", json=anth) as r:
            ev = [ln[7:] for ln in r.iter_lines() if ln.startswith("event:")]
            return r.status_code, r.headers, ev

    t0 = total()
    s1, h1, e1 = anth_stream()
    settle()
    s2, h2, e2 = anth_stream()
    check("Anthropic /v1/messages streaming: second request replayed from cache", s1 == s2 == 200 and h2.get("x-sg-cache") == "hit" and total() - t0 == 1, f"{h1.get('x-sg-cache')} {h2.get('x-sg-cache')} {total() - t0}")
    check("…with a complete Anthropic event sequence", e2[0] == "message_start" and "content_block_delta" in e2 and e2[-2:] == ["message_delta", "message_stop"] and set(e1) == set(e2), str(e2[:3]))

    print("[8] concurrency")
    t0 = total()
    with cf.ThreadPoolExecutor(10) as ex:
        runs = list(ex.map(lambda _: stream("s-on", "burst of identical streams"), range(10)))
    settle()
    nxt = stream("s-on", "burst of identical streams")
    check("10 simultaneous identical streams all complete correctly", all(r["status"] == 200 and r["finish"] == "stop" and r["text"] == runs[0]["text"] for r in runs) and total() - t0 == 10, f"{total() - t0}")
    check("the next identical request is a hit", nxt["headers"].get("x-sg-cache") == "hit" and nxt["text"] == runs[0]["text"])

    print("[10] dashboard performance numbers")
    long_prompt = "explain the architecture of a gateway in detail " * 25   # ~1200 chars -> ~300 prompt tokens in the mock
    for i in range(4):
        stream("s-on", f"{long_prompt} variant {i}")
    time.sleep(0.5)
    perf = api("GET", "/insights/performance?minutes=30")
    g = perf["gateways"][0]
    check("generation speed matches the mock's real rate (30 tokens at 50 ms each = ~20 tok/s)", g["gen_tps"] and 12 < g["gen_tps"]["p50"] < 28, str(g["gen_tps"]))
    check("prompt processing speed is reported for long prompts", g["prompt_tps"] and g["prompt_tps"]["p50"] > 500, str(g["prompt_tps"]))
    check("time to first token is about one token interval", g["ttft_ms"] and 30 < g["ttft_ms"]["p50"] < 400, str(g["ttft_ms"]))
    logs_all = api("GET", "/logs?limit=1000")
    check("cache hits/misses on the dashboard equal the request log",
          g["cache"]["hits"] == sum(1 for r in logs_all if r["cached"]) and g["cache"]["misses"] == sum(1 for r in logs_all if r["cache_state"] == "miss" and not r["cached"]),
          f"{g['cache']} vs {sum(1 for r in logs_all if r['cached'])}")
    check("hit rate = hits / (hits + misses)", abs(g["cache"]["hit_rate"] - g["cache"]["tracked_hits"] / (g["cache"]["tracked_hits"] + g["cache"]["misses"])) < 1e-9 and g["cache"]["stream_hits"] > 0)
    check("cache-hit replays are excluded from the speed numbers", g["gen_tps"]["n"] <= g["streamed"] - g["cache"]["stream_hits"])
    check("per-model table and timeline are filled", perf["models"] and perf["models"][0]["upstream"] == "plain-m" and any(p is not None for sr in perf["timeline"]["series"] for p in sr["points"]))
    check("skipped/bypassed requests are counted separately", g["cache"]["skipped"] >= 1 and g["cache"]["bypassed"] >= 1, str(g["cache"]))

    print("[9] logging and stats")
    logs = api("GET", "/logs?limit=200")
    hits = [r for r in logs if r["cached"] and r["stream"]]
    check("stream cache hits are logged as cached + stream with timing", hits and all(r["ttft_ms"] is not None and r["completion_tokens"] > 0 for r in hits[:3]), str(hits[:1]))
    cs = api("GET", "/cache/stats")
    check("cache stats count the hits", cs["hits"] >= 8 and cs["entries"] >= 8, str(cs))


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
