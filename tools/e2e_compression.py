#!/usr/bin/env python3
"""End-to-end checks for context compression, cache alignment and context insights.

Spawns mock upstreams + a throw-away gateway (own data dir). Never touches real endpoints or your data.
    python tools/e2e_compression.py
"""
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
GW = "http://127.0.0.1:8096"


def spawn(cmd, env, cwd):
    p = subprocess.Popen(cmd, env={**os.environ, **env}, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)


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


def mock(port, name, models, **env):
    spawn([PY, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "error"],
          {"MOCK_NAME": name, "MODELS": models, **{k: str(v) for k, v in env.items()}}, os.path.join(ROOT, "tools", "mock-llm"))
    wait(f"http://127.0.0.1:{port}/stats")


# ---- realistic tool outputs -------------------------------------------------------------------
def tool_json():
    return json.dumps([{"id": i, "name": f"user-{i}", "email": f"user{i}@example.com", "bio": "lorem ipsum dolor sit amet " * 6} for i in range(500)], indent=1)


def tool_log():
    return "\n".join(f"2024-05-01 10:{i // 60 % 60:02d}:{i % 60:02d} INFO worker-{i % 3} processed batch {i}" + (" ERROR timeout talking to db" if i % 400 == 0 else "") for i in range(2000))


def tool_grep():
    return "\n".join(f"src/module{f}/handler.py:{n}:    value = compute_something({n}, {f})  # TODO refactor" for f in range(40) for n in range(1, 31))


def tool_prose():
    return " ".join(f"Sentence number {i} explains how the build pipeline works in detail." for i in range(400))


def call(i, name):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}


def conversation(extra_user=None, system="You are a careful coding assistant.", live=True):
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": "Investigate the incident and summarize."},
            {"role": "assistant", "content": None, "tool_calls": [call(1, "list_users"), call(2, "read_log"), call(3, "grep_code"), call(4, "read_doc")]},
            {"role": "tool", "tool_call_id": "c1", "content": tool_json()}, {"role": "tool", "tool_call_id": "c2", "content": tool_log()},
            {"role": "tool", "tool_call_id": "c3", "content": tool_grep()}, {"role": "tool", "tool_call_id": "c4", "content": tool_prose()},
            {"role": "assistant", "content": "I have read everything. One more check."}, {"role": "user", "content": extra_user or "Please double check the last query."}]
    if live:
        msgs += [{"role": "assistant", "content": None, "tool_calls": [call(5, "run_query")]},
                 {"role": "tool", "tool_call_id": "c5", "content": json.dumps({"rows": [{"id": i, "v": "q" * 40} for i in range(60)]}, indent=1)}]
    return msgs


def chars(msgs):
    return sum(len(m["content"]) if isinstance(m.get("content"), str) else 0 for m in msgs)


def main():
    data = tempfile.mkdtemp(prefix="sgcomp-")
    mock(9301, "big", "big-m", CTX=200000, EXPOSE_PROPS=1, EXPOSE_META=1, SLOTS=4, TOKEN_DELAY=0.001)
    mock(9302, "small", "small-m", CTX=30000, EXPOSE_PROPS=1, EXPOSE_META=1, SLOTS=4, TOKEN_DELAY=0.001)
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8096", "--log-level", "error"], {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "30"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=60)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    big = api("POST", "/endpoints", json={"name": "big", "base_url": "http://127.0.0.1:9301/v1", "probe": False})["models"][0]["id"]
    small = api("POST", "/endpoints", json={"name": "small", "base_url": "http://127.0.0.1:9302/v1", "probe": False})["models"][0]["id"]
    api("POST", "/gateways", json={"name": "c", "slug": "c", "models": [
        {"public_name": "inherit", "members": [{"model_id": big}]},
        {"public_name": "on", "compress_mode": "on", "members": [{"model_id": big}]},
        {"public_name": "shadow", "compress_mode": "shadow", "members": [{"model_id": big}]},
        {"public_name": "off", "compress_mode": "off", "members": [{"model_id": big}]},
        {"public_name": "fit-on", "compress_mode": "on", "strategy": "priority_failover", "members": [{"model_id": small, "priority": 0}, {"model_id": big, "priority": 1}]},
        {"public_name": "fit-off", "compress_mode": "off", "strategy": "priority_failover", "members": [{"model_id": small, "priority": 0}, {"model_id": big, "priority": 1}]},
        {"public_name": "cached", "compress_mode": "on", "cache_enabled": True, "members": [{"model_id": big}]},
    ]})

    def chat(model, msgs, headers=None, **extra):
        return c.post("/gw/c/v1/chat/completions", json={"model": model, "messages": msgs, "max_tokens": 4, **extra}, headers=headers or {})

    def upstream_last(port=9301):
        return httpx.get(f"http://127.0.0.1:{port}/last").json()["messages"]

    orig = conversation()
    orig_chars = chars(orig)
    print(f"  original conversation: {orig_chars:,} chars (~{orig_chars // 4:,} tokens)")

    print("[1] global default off")
    r = chat("inherit", orig)
    got = upstream_last()
    check("off: forwarded byte-identical", r.status_code == 200 and got == orig and "x-sg-compressed" not in r.headers)
    check("off: no compression events", api("GET", "/insights/compression?range=24h")["totals"]["compressionEventsCount"] == 0)

    print("[2] mode on (per-model)")
    r = chat("on", orig)
    got = upstream_last()
    check("on: request succeeds and prompt is much smaller", r.status_code == 200 and chars(got) < orig_chars * 0.25, f"{chars(got)} vs {orig_chars}")
    check("X-SG-Compressed header reports tokens saved", int(r.headers.get("x-sg-compressed", 0)) > 5000, r.headers.get("x-sg-compressed", ""))
    check("live zone (last tool result) forwarded byte-identical", got[-1] == orig[-1] and got[-2] == orig[-2])
    check("system/user/assistant text untouched", all(got[i] == orig[i] for i in (0, 1, 2, 7, 8, 9)))
    check("older tool results were shrunk, structure kept", all(len(got[i]["content"]) < len(orig[i]["content"]) for i in (3, 4, 5, 6)) and [m["tool_call_id"] for m in got[3:7]] == ["c1", "c2", "c3", "c4"])
    check("compressed JSON is still valid JSON", isinstance(json.loads(got[3]["content"]), list))
    check("log keeps its ERROR lines", "ERROR timeout talking to db" in got[4]["content"] and "log lines compressed" in got[4]["content"])
    check("grep result keeps paths and line numbers", "src/module0/handler.py:1:" in got[5]["content"] and "more files" in got[5]["content"])

    print("[3] determinism / cache")
    first_forward = [m["content"] for m in got[3:7]]
    r2 = chat("on", conversation(extra_user="Another question entirely."))
    got2 = upstream_last()
    check("next turn: identical compressed prefix (prompt-cache friendly)", [m["content"] for m in got2[3:7]] == first_forward)
    cs = api("GET", "/compression/cache")
    check("compression cache served hits on the second turn", cs["hits"] >= 4 and cs["entries"] >= 4, str(cs))

    print("[4] shadow mode")
    t_before = api("GET", "/insights/compression?range=24h")["totals"]
    r = chat("shadow", conversation(extra_user="shadow turn"))
    got = upstream_last()
    check("shadow: forwarded unchanged", got == conversation(extra_user="shadow turn") and "x-sg-compressed" not in r.headers)
    t_after = api("GET", "/insights/compression?range=24h")["totals"]
    check("shadow: events recorded as not applied, potential savings measured", t_after["shadowEvents"] > t_before["shadowEvents"] and t_after["potentialTokensNotSent"] > 0, str(t_after))
    check("shadow does not inflate 'tokens not sent'", t_after["tokensNotSent"] == t_before["tokensNotSent"])

    print("[5] overrides")
    api("PUT", "/settings/compression", json={"mode": "on"})
    time.sleep(3.5)  # settings are cached for 3 s on the hot path
    chat("inherit", conversation(extra_user="global on"))
    check("global 'on' applies to models that inherit", chars(upstream_last()) < orig_chars * 0.25)
    chat("off", conversation(extra_user="model off"))
    check("per-model 'off' beats global 'on'", chars(upstream_last()) >= orig_chars * 0.9)
    chat("inherit", conversation(extra_user="client opt-out"), headers={"X-SG-Compress": "off"})
    check("client header X-SG-Compress: off opts out", chars(upstream_last()) >= orig_chars * 0.9)
    api("PUT", "/settings/compression", json={"mode": "off"})
    time.sleep(3.5)

    print("[6] compression makes a long conversation fit a small context window")
    r = chat("fit-off", orig)
    check("without compression only the big-context model can take it", r.status_code == 200 and r.headers.get("x-sg-upstream") == "big-m", f"{r.status_code} {r.headers.get('x-sg-upstream')}")
    r = chat("fit-on", orig)
    check("with compression the small-context model (preferred) now fits", r.status_code == 200 and r.headers.get("x-sg-upstream") == "small-m", f"{r.status_code} {r.headers.get('x-sg-upstream')} {r.text[:120]}")

    print("[7] Anthropic /v1/messages surface")
    anth = {"model": "on", "max_tokens": 4, "system": "be brief", "messages": [
        {"role": "user", "content": "check"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read_log", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": tool_log()}]},
        {"role": "assistant", "content": "ok"}, {"role": "user", "content": "and now?"}]}
    r = c.post("/gw/c/v1/messages", json=anth)
    got = upstream_last()
    tool_msgs = [m for m in got if m["role"] == "tool"]
    check("tool_result blocks from Anthropic clients are compressed too", r.status_code == 200 and tool_msgs and len(tool_msgs[0]["content"]) < len(tool_log()) * 0.3, f"{r.status_code}")

    print("[8] response cache key is independent of compression")
    before = httpx.get("http://127.0.0.1:9301/stats").json()["total"]
    a = chat("cached", conversation(extra_user="cache me"), temperature=0)
    b = chat("cached", conversation(extra_user="cache me"), temperature=0)
    after = httpx.get("http://127.0.0.1:9301/stats").json()["total"]
    check("identical request is a response-cache hit and skips the upstream", a.headers.get("x-sg-cache") == "miss" and b.headers.get("x-sg-cache") == "hit" and after - before == 1, f"{a.headers.get('x-sg-cache')} {b.headers.get('x-sg-cache')} {after - before}")

    print("[9] cache alignment")
    chat("inherit", conversation(system="Today is 2024-05-01T10:20:30Z. Session 123e4567-e89b-12d3-a456-426614174000.", extra_user="aligned?"))
    rows = api("GET", "/logs?limit=3")
    check("volatile system prompt content lowers the alignment score", rows[0]["alignment_score"] == 80 and rows[0]["volatile_findings"] == 2 and rows[0]["volatile_labels"] == {"iso8601": 1, "uuid": 1}, str({k: rows[0].get(k) for k in ('alignment_score', 'volatile_labels')}))
    check("clean system prompt scores 100", any(r_["alignment_score"] == 100 for r_ in api("GET", "/logs?limit=20")))
    al = api("GET", "/insights/compression?range=24h")["alignment"]
    check("alignment insights: buckets, labels, prompt variants", al["scoreBuckets"]["70-89"] >= 1 and al["volatileLabels"].get("uuid", 0) >= 1 and al["promptVariants"] and al["promptVariants"][0]["variants"] >= 1, str(al))

    print("[10] insights")
    ci = api("GET", "/insights/compression?range=24h")
    t = ci["totals"]
    check("totals: events, applied and shadow separated", t["compressionEventsCount"] > 0 and t["appliedEvents"] > 0 and t["shadowEvents"] > 0)
    check("savings: unique (fresh) vs every-turn counted separately", t["uniqueTokensSaved"] > 0 and t["tokensNotSent"] > t["uniqueTokensSaved"], str(t))
    check("cache hit rate and ratio reported", 0 < t["cacheHitRate"] < 1 and 0 < t["avgCompressionRatio"] < 1)
    types = {r_["key"] for r_ in ci["byContentType"]}
    comps = {r_["key"] for r_ in ci["byCompressor"]}
    check("breakdown by content type and compressor", {"json", "log"} <= types and {"JSONCompressor", "LogCompressor", "cache_hit"} <= comps, f"{types} {comps}")
    gw_id = api("GET", "/gateways")[0]["id"]
    check("gateway filter works", api("GET", f"/insights/compression?range=24h&gateway_id={gw_id}")["totals"]["compressionEventsCount"] == t["compressionEventsCount"] and api("GET", "/insights/compression?range=24h&gateway_id=9999")["totals"]["compressionEventsCount"] == 0)
    for _ in range(3):
        chat("inherit", conversation(extra_user="growth"), headers={"X-SG-Session": "sess-A"})
        time.sleep(0.2)
    cx = api("GET", "/insights/context?range=24h&top=5")
    comp = cx["composition"]
    check("context composition shares sum to 1 and messages dominate", comp["requests"] > 5 and abs(sum(comp["shares"].values()) - 1) < 1e-6 and comp["shares"]["messages"] > 0.9, str(comp["shares"]))
    top = cx["contributors"][0]
    check("top contributors: re-sent tool results grouped by hash", top["kind"] == "tool_result" and top["occurrences"] >= 3 and top["totalChars"] == top["chars"] * top["occurrences"], str(top))
    sess = [s_ for s_ in cx["growth"] if s_["sessionKey"].startswith("h:sess-A")]
    check("growth curve per session with exact token counts", sess and sess[0]["requests"] == 3 and all(p["promptTokens"] > 0 for p in sess[0]["points"]), str(cx["growth"][:2]))
    check("no request content stored in the log", "double check the last query" not in json.dumps(api("GET", "/logs?limit=50")) and "Sentence number 1 explains" not in json.dumps(api("GET", "/logs?limit=50")))

    print("[11] preview tool + settings")
    p = api("POST", "/compression/preview", json={"text": tool_log()})
    check("preview reports detection, compressor and savings", p["detected"]["content_type"] == "log" and p["compressor_used"] == "LogCompressor" and p["tokens_saved"] > 1000, str(p)[:200])
    check("compression settings round-trip", api("GET", "/settings/compression")["mode"] == "off")


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
