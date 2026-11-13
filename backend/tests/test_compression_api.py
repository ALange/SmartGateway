import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture(scope="module")
def client():
    db.init_db()
    return TestClient(app)


def test_settings_defaults_roundtrip_and_validation(client):
    s = client.get("/api/settings/compression").json()
    assert s["mode"] == "off" and s["context_insights_enabled"] is True and s["config"]["min_content_chars"] == 100 and s["defaults"]["json"]["max_items"] == 15
    r = client.put("/api/settings/compression", json={"mode": "shadow", "config": {"json": {"max_items": 5}, "enable_log": False}})
    assert r.status_code == 200
    s = client.get("/api/settings/compression").json()
    assert s["mode"] == "shadow" and s["config"]["json"]["max_items"] == 5 and s["config"]["enable_log"] is False and s["config"]["log"]["max_lines"] == 100
    assert client.put("/api/settings/compression", json={"mode": "turbo"}).status_code == 400
    assert client.put("/api/settings/compression", json={"config": {"json": {"compression_ratio": 5}}}).status_code == 400
    assert client.put("/api/settings/compression", json={"config": {"log": {"max_lines": 1}}}).status_code == 400
    assert client.get("/api/settings/compression").json()["config"]["json"]["max_items"] == 5  # rejected updates change nothing
    client.put("/api/settings/compression", json={"mode": "off", "config": {}})


def test_preview(client):
    text = json.dumps([{"id": i, "name": f"n{i}", "description": "d" * 80} for i in range(80)])
    r = client.post("/api/compression/preview", json={"text": text}).json()
    assert r["detected"]["content_type"] == "json" and r["compressor_used"] == "JSONCompressor" and r["tokens_saved"] > 0
    assert r["chars_after"] < r["chars_before"] and json.loads(r["compressed"])
    r2 = client.post("/api/compression/preview", json={"text": text, "config": {"enable_json": False}}).json()
    assert r2["compressor_used"] != "JSONCompressor"
    assert client.post("/api/compression/preview", json={"text": "x" * 1_000_001}).status_code == 413
    assert client.post("/api/compression/preview", json={"text": "hi", "config": {"min_content_chars": -5}}).status_code == 400
    assert client.post("/api/compression/preview", json={"text": ""}).json()["compressor_used"] == "passthrough"


def test_cache_endpoints(client):
    assert set(client.get("/api/compression/cache").json()) >= {"entries", "hits", "misses", "max_entries"}
    assert "cleared" in client.post("/api/compression/cache/clear").json()


def test_insights_empty_shapes_and_range_validation(client):
    c = client.get("/api/insights/compression?range=24h").json()
    assert c["totals"]["compressionEventsCount"] == 0 and c["byContentType"] == [] and c["alignment"]["scoreBuckets"]["90-100"] == 0 and c["liveCache"]
    x = client.get("/api/insights/context?range=7d&top=3").json()
    assert x["composition"]["requests"] == 0 and x["growth"] == [] and x["meta"]["charsPerToken"] == 4
    assert client.get("/api/insights/compression?range=forever").status_code == 400


def test_gateway_model_compress_mode_validation(client):
    body = {"name": "g", "slug": "comp-test", "models": [{"public_name": "m", "compress_mode": "turbo", "members": []}]}
    assert client.post("/api/gateways", json=body).status_code == 400
    body["models"][0]["compress_mode"] = "shadow"
    g = client.post("/api/gateways", json=body).json()
    assert g["models"][0]["compress_mode"] == "shadow"
    body["models"][0]["compress_mode"] = ""
    g2 = client.put(f"/api/gateways/{g['id']}", json=body).json()
    assert g2["models"][0]["compress_mode"] is None  # '' = inherit
    client.delete(f"/api/gateways/{g['id']}")


def test_insights_with_data(client):
    """Regression: aggregation queries must work on real rows, not only on an empty DB."""
    import time
    from sqlmodel import Session
    from app.models import CompressionEvent, RequestLog
    now = time.time()
    with Session(db.engine) as s:
        for i in range(3):
            s.add(RequestLog(gateway_id=77, public_model="m", ts=now, compress_mode="on", tokens_saved=100, alignment_score=80 if i else 100,
                             volatile_findings=2 if i else 0, volatile_labels={"uuid": 1, "iso8601": 1} if i else None, prefix_hash=f"h{i % 2}",
                             session_key="h:s", prompt_tokens=500 + i, completion_tokens=5, ctx_system_chars=100, ctx_tools_chars=0,
                             ctx_messages_chars=4000, ctx_blocks=[{"hash": "aa", "kind": "tool_result", "label": "read", "chars": 2000}]))
        s.add(CompressionEvent(gateway_id=77, public_model="m", content_type="json", compressor_used="JSONCompressor", strategy="sampled_15", compression_ratio=0.2,
                               tokens_before=1000, tokens_after=200, chars_before=4000, chars_after=800, cache_hit=False, applied=True))
        s.add(CompressionEvent(gateway_id=77, public_model="m", compressor_used="cache_hit", compression_ratio=0.2, tokens_before=1000, tokens_after=200, cache_hit=True, applied=True))
        s.add(CompressionEvent(gateway_id=77, public_model="m", content_type="log", compressor_used="LogCompressor", compression_ratio=0.5, tokens_before=400, tokens_after=200, cache_hit=False, applied=False))
        s.commit()
    r = client.get("/api/insights/compression?range=1h&gateway_id=77").json()
    t = r["totals"]
    assert t["compressionEventsCount"] == 3 and t["appliedEvents"] == 2 and t["shadowEvents"] == 1 and t["requests"] == 3
    assert t["uniqueTokensSaved"] == 800 + 200  # fresh events only: the cache-hit row must not add its 800 again
    assert t["tokensNotSent"] == 300 and abs(t["cacheHitRate"] - 1 / 3) < 1e-9 and abs(t["avgCompressionRatio"] - 0.35) < 1e-9
    by = {x["key"]: x for x in r["byContentType"]}
    assert by["json"]["tokensSaved"] == 800 and by["Unknown"]["events"] == 1 and by["Unknown"]["tokensSaved"] == 0
    assert {x["key"] for x in r["byCompressor"]} == {"JSONCompressor", "cache_hit", "LogCompressor"}
    al = r["alignment"]
    assert al["scoreBuckets"]["90-100"] == 1 and al["scoreBuckets"]["70-89"] == 2 and al["volatileLabels"] == {"uuid": 2, "iso8601": 2}
    assert al["requestsWithFindings"] == 2 and al["promptVariants"][0]["variants"] == 2 and abs(al["avgScore"] - 86.666) < 0.01
    cx = client.get("/api/insights/context?range=1h&gateway_id=77").json()
    assert cx["composition"]["requests"] == 3 and cx["tokenTotals"]["prompt"] == 1503 and cx["contributors"][0]["occurrences"] == 3
    assert cx["growth"][0]["requests"] == 3
