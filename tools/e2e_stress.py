#!/usr/bin/env python3
"""End-to-end checks for the gateway stress test (temporary mock upstream with 4 slots at ~50 tok/s per request, throw-away gateway)."""
import os
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
procs, results = [], []
GW = "http://127.0.0.1:8087"


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
    data = tempfile.mkdtemp(prefix="sgstress-")
    spawn([PY, "-m", "uvicorn", "main:app", "--port", "9501", "--log-level", "error"],
          {"MOCK_NAME": "m", "MODELS": "plain-m", "SLOTS": "4", "EXPOSE_PROPS": "1", "TOKEN_DELAY": "0.02", "TOKENS": "40"}, os.path.join(ROOT, "tools", "mock-llm"))
    wait("http://127.0.0.1:9501/stats")
    spawn([PY, "-m", "uvicorn", "app.main:app", "--port", "8087", "--log-level", "error"], {"SG_DATA_DIR": data, "SG_HEALTH_INTERVAL": "60"}, os.path.join(ROOT, "backend"))
    wait(GW + "/healthz")
    c = httpx.Client(base_url=GW, timeout=60)

    def api(method, path, **kw):
        r = c.request(method, "/api" + path, **kw)
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r.json()

    mid = api("POST", "/endpoints", json={"name": "m", "base_url": "http://127.0.0.1:9501/v1", "probe": False})["models"][0]["id"]
    gw = api("POST", "/gateways", json={"name": "stress-gw", "slug": "sg", "models": [{"public_name": "m", "cache_enabled": True, "cache_streaming": True, "members": [{"model_id": mid}]}]})
    url = f"/gateways/{gw['id']}/stress"
    logs_before = len(api("GET", "/logs?limit=1000"))
    cache_before = api("GET", "/cache/stats")

    print("[1] starting")
    check("a stress test needs explicit acknowledgement", c.post("/api" + url, json={}).status_code == 400)
    check("invalid settings are rejected", c.post("/api" + url, json={"acknowledge": True, "levels": [1, 500]}).status_code == 400)
    job = api("POST", url, json={"acknowledge": True, "levels": [1, 2, 4, 8], "stage_seconds": 6, "prompt_tokens": 300, "max_tokens": 64})
    check("job started with the detected slot count", job["slots"] == 4 and job["job_id"] and job["config"]["levels"] == [1, 2, 4, 8], str(job))
    second = c.post("/api" + url, json={"acknowledge": True, "levels": [1]})
    check("only one stress test can run at a time (409)", second.status_code == 409 and second.json()["detail"]["job_id"] == job["job_id"], second.text[:120])
    check("/stress/current re-attaches to the running job", api("GET", "/stress/current")["job_id"] == job["job_id"])

    print("[2] live progress")
    seen_stage, max_inflight, max_tps, snap = set(), 0, 0.0, {}
    while True:
        snap = api("GET", f"/stress/{job['job_id']}")
        if snap["current"]:
            seen_stage.add(snap["stage_index"])
            max_inflight = max(max_inflight, snap["current"]["inflight"])
            max_tps = max(max_tps, snap["current"]["tps_now"])
        if snap["status"] != "running":
            break
        time.sleep(0.5)
    check("progress reported stage by stage", seen_stage == {0, 1, 2, 3} and snap["stage_count"] == 4, str(seen_stage))
    check("live in-flight count and token rate were reported", max_inflight >= 4 and max_tps > 20, f"{max_inflight} {max_tps}")

    print("[3] results")
    st = {s["concurrency"]: s for s in snap["stages"]}
    check("finished normally with all four stages", snap["status"] == "done" and sorted(st) == [1, 2, 4, 8] and not snap["stop_reason"], f"{snap['status']} {snap['stop_reason']} {sorted(st)}")
    one = st[1]["gen_tps"]["p50"]
    check("single-stream speed matches the mock (~50 tok/s per request)", 30 < one < 56, str(one))
    check("throughput scales with concurrency up to the slot count", 1.6 < st[2]["agg_tps"] / st[1]["agg_tps"] < 2.3 and 3.0 < st[4]["agg_tps"] / st[1]["agg_tps"] < 4.6,
          f"{st[1]['agg_tps']:.0f} {st[2]['agg_tps']:.0f} {st[4]['agg_tps']:.0f}")
    check("beyond the slot count throughput stops growing and requests queue", st[8]["agg_tps"] < 1.3 * st[4]["agg_tps"] and st[8]["queue_ms"]["p50"] > 200 and st[4]["queue_ms"]["p50"] < 100,
          f"{st[8]['agg_tps']:.0f} vs {st[4]['agg_tps']:.0f}, queue {st[8]['queue_ms']['p50']:.0f}/{st[4]['queue_ms']['p50']:.0f}")
    check("no errors at any level", all(s["errors"] == 0 for s in snap["stages"]))
    sm_ = snap["summary"]
    check("peak is found at the saturated levels (4-8)", sm_["peak"]["concurrency"] in (4, 8) and sm_["peak"]["agg_tps"] > 150, str(sm_["peak"]))
    check("recommended concurrency = slot count, saturation starts at 8", sm_["recommended_concurrency"] == 4 and sm_["saturation_concurrency"] == 8 and sm_["slots"] == 4, str({k: sm_[k] for k in ('recommended_concurrency', 'saturation_concurrency', 'slots')}))
    check("a plain-language verdict is produced", len(sm_["verdict"]) >= 4 and "Peak throughput" in sm_["verdict"][0])
    check("requests were spread over the upstream", all(sum(s["upstreams"].values()) == s["completed"] for s in snap["stages"]))
    check("the mock really received the load", httpx.get("http://127.0.0.1:9501/stats").json()["total"] > 60)

    print("[4] no side effects on logs, stats or caches")
    check("stress traffic is not written to the request log", len(api("GET", "/logs?limit=1000")) == logs_before)
    cache_after = api("GET", "/cache/stats")
    check("response cache untouched (bypassed)", cache_after["stores"] == cache_before["stores"] and cache_after["hits"] == cache_before["hits"], str(cache_after))
    check("dashboard numbers not distorted", api("GET", "/insights/performance?minutes=60")["gateways"] == [])

    print("[5] history")
    runs = api("GET", f"/gateways/{gw['id']}/stress/runs")
    check("the run is stored with its summary", len(runs) == 1 and runs[0]["status"] == "done" and runs[0]["summary"]["peak"]["concurrency"] == sm_["peak"]["concurrency"] and runs[0]["config"]["slots"] == 4)
    full = api("GET", f"/stress/runs/{runs[0]['id']}")
    check("full results can be fetched later", len(full["result"]["stages"]) == 4 and full["result"]["summary"]["peak"])

    print("[6] stopping")
    j2 = api("POST", url, json={"acknowledge": True, "levels": [2, 4], "stage_seconds": 30, "prompt_tokens": 100, "max_tokens": 64})
    time.sleep(4)
    api("POST", f"/stress/{j2['job_id']}/stop")
    time.sleep(1.0)
    s2 = api("GET", f"/stress/{j2['job_id']}")
    check("stop ends the test immediately and keeps the partial stage", s2["status"] == "stopped" and s2["stop_reason"] == "stopped by user" and len(s2["stages"]) == 1 and s2["stages"][0]["interrupted"], str(s2["status"]))
    time.sleep(2.5)
    live = api("GET", "/live")["models"][str(mid)]
    check("all slots are released after stopping", live["in_flight"] == 0 and httpx.get("http://127.0.0.1:9501/stats").json()["in_flight"] == 0, str(live))
    check("a new test can be started after stopping", c.post("/api" + url, json={"acknowledge": True, "levels": [1], "stage_seconds": 5}).status_code == 200)
    while api("GET", "/stress/current"):
        time.sleep(0.5)

    print("[6b] prose and code scenarios")
    check("an unknown scenario is rejected", c.post("/api" + url, json={"acknowledge": True, "levels": [1], "scenario": "poetry"}).status_code == 400)
    sent = {}
    for scen in ("code", "prose"):
        jj = api("POST", url, json={"acknowledge": True, "levels": [1], "stage_seconds": 5, "prompt_tokens": 400, "max_tokens": 32, "scenario": scen})
        while api("GET", f"/stress/{jj['job_id']}")["status"] == "running":
            time.sleep(0.4)
        sent[scen] = httpx.get("http://127.0.0.1:9501/last").json()["messages"][-1]["content"]
        done = api("GET", f"/stress/{jj['job_id']}")
        check(f"{scen}: the test completes and records its scenario", done["status"] == "done" and done["config"]["scenario"] == scen and done["stages"][0]["completed"] >= 1, str(done["config"]))
    check("code scenario sends source code and asks for code only", "def " in sent["code"] and "class " in sent["code"] and "Output only code" in sent["code"] and "write a long" not in sent["code"])
    check("prose scenario sends prose and asks for a long analysis", "write a long, detailed" in sent["prose"] and "def " not in sent["prose"])
    check("history remembers the scenario of each run", [r["config"].get("scenario") for r in api("GET", f"/gateways/{gw['id']}/stress/runs")][:2] == ["prose", "code"])

    print("[7] failing upstream")
    httpx.post("http://127.0.0.1:9501/admin/fail/1")
    j3 = api("POST", url, json={"acknowledge": True, "levels": [1, 2, 4], "stage_seconds": 5, "prompt_tokens": 100, "max_tokens": 32})
    while True:
        s3 = api("GET", f"/stress/{j3['job_id']}")
        if s3["status"] != "running":
            break
        time.sleep(0.5)
    httpx.post("http://127.0.0.1:9501/admin/fail/0")
    check("a failing gateway stops the ramp early instead of hammering it", s3["status"] == "done" and "stopped early" in (s3["stop_reason"] or "") and len(s3["stages"]) == 1, str(s3["stop_reason"]))
    check("errors are counted and classified", s3["stages"][0]["errors"] >= 1 and s3["stages"][0]["completed"] == 0 and s3["stages"][0]["error_breakdown"], str(s3["stages"][0]["error_breakdown"]))
    check("the summary explains that nothing could be measured", s3["summary"]["peak"] is None and "No stage completed" in s3["summary"]["verdict"][0])
    check("history keeps all runs, newest first", [r["status"] for r in api("GET", f"/gateways/{gw['id']}/stress/runs")][:1] == ["done"] and len(api("GET", f"/gateways/{gw['id']}/stress/runs")) == 6)


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
