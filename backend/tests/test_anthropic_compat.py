import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db, proxy
from app.compat import anthropic as anth
from app.main import app
from app.models import Endpoint, Model, RequestLog
from app.router import health


async def _lines(items):
    for i in items:
        yield i


# ------------------------------------------------------------------ pure translation
def test_to_chat_strict_options_and_default_unchanged():
    body = {"model": "m", "max_tokens": 9, "top_k": 5, "thinking": {"type": "enabled", "budget_tokens": 2000},
            "messages": [{"role": "user", "content": "a"}, {"role": "user", "content": [{"type": "text", "text": "b"}]},
                         {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm", "signature": "x"}, {"type": "text", "text": "ok"}]}],
            "tools": [{"name": "f", "input_schema": {"type": "object"}}], "tool_choice": {"type": "none"}}
    s = anth.to_chat(body, True)
    assert s["messages"][0] == {"role": "user", "content": "a\n\nb"} and s["messages"][1] == {"role": "assistant", "content": "ok"}
    assert s["tool_choice"] == "none" and s["top_k"] == 5 and s["reasoning_effort"] == "low"
    d = anth.to_chat(body)  # default behaviour is untouched
    assert len(d["messages"]) == 3 and "tool_choice" not in d and "top_k" not in d and "reasoning_effort" not in d
    assert anth.to_chat({"messages": [], "thinking": {"type": "enabled", "budget_tokens": 20000}}, True)["reasoning_effort"] == "high"


def test_from_chat_strict_thinking_usage_refusal():
    resp = {"choices": [{"message": {"content": "hi", "reasoning_content": "thinking..."}, "finish_reason": "content_filter"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 2}}}
    r = anth.from_chat(resp, "pub", True)
    assert r["content"][0] == {"type": "thinking", "thinking": "thinking...", "signature": ""} and r["content"][1]["text"] == "hi"
    assert r["stop_reason"] == "refusal"
    assert r["usage"] == {"input_tokens": 3, "output_tokens": 4, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2}
    plain = anth.from_chat(resp, "pub")
    assert plain["content"] == [{"type": "text", "text": "hi"}] and plain["usage"] == {"input_tokens": 3, "output_tokens": 4}


def test_error_body_status_mapping():
    assert anth.error_body("x", "api_error", 429, "req_1") == {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}, "request_id": "req_1"}
    assert anth.error_body("x", "api_error", 529)["error"]["type"] == "overloaded_error"
    assert anth.error_body("x", "invalid_api_key", 503)["error"]["type"] == "api_error"
    assert anth.error_body("x", "not_found")["error"]["type"] == "not_found_error" and "request_id" not in anth.error_body("x")


async def test_stream_strict_thinking_ping_input_tokens_and_error():
    chunks = ['data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}', 'data: {"choices":[{"delta":{"content":"Hi"}}]}',
              'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
              'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2}}', "data: [DONE]"]
    out = [e async for e in anth.stream_from_chat(_lines(chunks), "pub", True, 9)]
    names = [e.split("\n")[0] for e in out]
    assert names == ["event: message_start", "event: ping", "event: content_block_start", "event: content_block_delta",
                     "event: content_block_delta", "event: content_block_start", "event: content_block_delta", "event: content_block_stop",
                     "event: message_delta", "event: message_stop"]
    assert json.loads(out[0].split("data: ")[1])["message"]["usage"]["input_tokens"] == 9
    assert '"signature_delta"' in out[4] and "event: content_block_stop" in out[4] and '"thinking_delta"' in out[3]
    assert json.loads(out[8].split("data: ")[1])["usage"] == {"input_tokens": 11, "output_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    async def broken():
        yield 'data: {"choices":[{"delta":{"content":"a"}}]}'
        raise RuntimeError("boom")
    out = [e async for e in anth.stream_from_chat(broken(), "pub", True)]
    assert out[-1].startswith("event: error") and "boom" not in out[-1]      # the public gets a generic message ...
    from app.runtime_ctx import verbose
    tok = verbose.set(True)
    try:
        out = [e async for e in anth.stream_from_chat(broken(), "pub", True)]
    finally:
        verbose.reset(tok)
    assert out[-1].startswith("event: error") and "boom" in out[-1]           # ... the admin sees the cause
    with pytest.raises(RuntimeError):
        [e async for e in anth.stream_from_chat(broken(), "pub")]


async def test_ping_when_upstream_is_quiet(monkeypatch):
    import asyncio
    monkeypatch.setattr(anth, "PING_EVERY", 0.05)

    async def slow():
        await asyncio.sleep(0.2)
        yield 'data: {"choices":[{"delta":{"content":"a"}}]}'
    out = [e async for e in anth.stream_from_chat(slow(), "pub", True)]
    assert sum(1 for e in out if e.startswith("event: ping")) >= 3


# ------------------------------------------------------------------ gateway end to end
@pytest.fixture(scope="module")
def client():
    db.init_db()
    yield TestClient(app)
    with Session(db.engine) as s:  # other modules assume an empty request log
        for r in s.exec(select(RequestLog)).all():
            s.delete(r)
        s.commit()


@pytest.fixture
def fake(monkeypatch):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        if body.get("stream"):
            parts = [{"choices": [{"index": 0, "delta": {"reasoning_content": "pondering"}}]},
                     {"choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": "stop"}]},
                     {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}]
            sse = "".join(f"data: {json.dumps(p)}\n\n" for p in parts) + "data: [DONE]\n\n"
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
        if body["messages"][-1]["content"] == "fail":
            return httpx.Response(500, text="upstream down")
        return httpx.Response(200, json={"id": "x", "model": body["model"], "choices": [{"index": 0, "message": {
            "role": "assistant", "content": "Hello", "reasoning_content": "pondering"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}})
    monkeypatch.setattr(proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def make(client):
    with Session(db.engine) as s:
        ep = Endpoint(name=f"ac-ep-{time.time_ns()}", base_url="http://fake/v1", status="online")
        s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="up", capabilities=["chat"], max_slots=4, context_length=32000)
        s.add(m); s.commit(); s.refresh(m)
        ep_id, m_id = ep.id, m.id
    health.status[ep_id] = "online"
    r = client.post("/api/gateways", json={"name": f"ac-{time.time_ns()}", "models": [{"public_name": "claude-x", "members": [{"model_id": m_id}]}]})
    assert r.status_code == 200, r.text
    return r.json()


ANTH = {"anthropic-version": "2023-06-01"}


def msg(client, gw, content="hi", **kw):
    return client.post(f"/api/admin-gw/{gw['slug']}/v1/messages",
                       json={"model": "claude-x", "max_tokens": 50, "messages": [{"role": "user", "content": content}], **kw})


def chat(client, gw, content="hi", **kw):
    return client.post(f"/api/admin-gw/{gw['slug']}/v1/chat/completions", json={"model": "claude-x", "messages": [{"role": "user", "content": content}], **kw})


def test_one_gateway_speaks_both_apis(client, fake):
    gw = make(client)
    a = msg(client, gw)
    assert a.status_code == 200 and a.headers["request-id"].startswith("req_")
    j = a.json()
    assert j["type"] == "message" and j["content"][0]["type"] == "thinking" and j["content"][1]["text"] == "Hello"
    assert j["usage"]["cache_read_input_tokens"] == 0
    o = chat(client, gw).json()  # the OpenAI side is untouched
    assert o["choices"][0]["message"]["content"] == "Hello" and "request-id" not in chat(client, gw).headers


def test_stream_through_gateway(client, fake):
    gw = make(client)
    r = msg(client, gw, stream=True)
    assert r.status_code == 200 and r.headers["request-id"]
    text = r.text
    assert "event: ping" in text and '"thinking_delta"' in text and '"text_delta"' in text and "event: message_stop" in text
    first = json.loads(text.split("event: message_start\ndata: ")[1].split("\n")[0])
    assert first["message"]["usage"]["input_tokens"] >= 0  # local estimate (the upstream usage arrives at the end)
    o = chat(client, gw, stream=True).text  # OpenAI streaming is not rewritten
    assert "event: ping" not in o and "choices" in o


def test_models_shape_follows_the_client(client, fake):
    gw = make(client)
    base = f"/api/admin-gw/{gw['slug']}/v1"
    m = client.get(f"{base}/models", headers=ANTH).json()
    assert m["data"][0]["type"] == "model" and m["data"][0]["id"] == "claude-x" and m["has_more"] is False and m["first_id"] == m["last_id"] == "claude-x"
    assert client.get(f"{base}/models", headers={"x-api-key": "k"}).json()["data"][0]["type"] == "model"   # x-api-key alone = Anthropic client
    assert client.get(f"{base}/models", headers={"x-api-key": "k", "Authorization": "Bearer k"}).json()["object"] == "list"
    assert client.get(f"{base}/models").json()["object"] == "list"                                          # plain OpenAI client
    one = client.get(f"{base}/models/claude-x", headers=ANTH).json()
    assert one["type"] == "model" and one["display_name"] == "claude-x"
    miss = client.get(f"{base}/models/nope", headers=ANTH)
    assert miss.status_code == 404 and miss.json()["error"]["type"] == "not_found_error"


def test_error_shape_follows_the_client(client, fake):
    gw = make(client)
    e = msg(client, gw, "fail")
    assert e.status_code >= 500 and e.json()["type"] == "error" and e.json()["error"]["type"] == "api_error" and e.json()["request_id"] == e.headers["request-id"]
    bad = client.post(f"/api/admin-gw/{gw['slug']}/v1/nonsense", json={}, headers=ANTH)
    assert bad.status_code == 404 and bad.json()["type"] == "error" and bad.json()["error"]["type"] == "not_found_error"
    oa = chat(client, gw, "fail")  # an OpenAI client keeps the OpenAI error shape
    assert oa.status_code >= 500 and "error" in oa.json() and oa.json().get("type") != "error"


def test_count_tokens_includes_system_and_tools(client):
    gw = make(client)
    base = {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}
    n0 = client.post(f"/api/admin-gw/{gw['slug']}/v1/messages/count_tokens", json=base).json()["input_tokens"]
    n1 = client.post(f"/api/admin-gw/{gw['slug']}/v1/messages/count_tokens",
                     json={**base, "system": "s" * 700, "tools": [{"name": "f", "description": "d" * 700, "input_schema": {"type": "object"}}]}).json()["input_tokens"]
    assert n1 > n0 + 300


def test_model_aliases(client, fake):
    with Session(db.engine) as s:
        ep = Endpoint(name=f"al-ep-{time.time_ns()}", base_url="http://fake/v1", status="online")
        s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="up", capabilities=["chat"], max_slots=4, context_length=32000)
        s.add(m); s.commit(); s.refresh(m)
        ep_id, m_id = ep.id, m.id
    health.status[ep_id] = "online"
    body = {"name": f"al-{time.time_ns()}", "models": [{"public_name": "main", "aliases": ["gpt-4o", " claude-sonnet ", "GPT-4O", "main", ""], "members": [{"model_id": m_id}]}]}
    res = client.post("/api/gateways", json=body)
    assert res.status_code == 200, res.text
    gw = res.json()
    assert gw["models"][0]["aliases"] == ["gpt-4o", "claude-sonnet"]            # trimmed, de-duplicated, own name dropped
    base = f"/api/admin-gw/{gw['slug']}/v1"
    ids = [m["id"] for m in client.get(f"{base}/models").json()["data"]]
    assert ids == ["main", "gpt-4o", "claude-sonnet"]
    assert "alias_of" not in client.get(f"{base}/models").json()["data"][1]
    for name, shown in (("main", "main"), ("gpt-4o", "gpt-4o"), ("CLAUDE-SONNET", "claude-sonnet")):
        r = client.post(f"{base}/chat/completions", json={"model": name, "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200 and r.json()["model"] == shown
    miss = client.post(f"{base}/chat/completions", json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]})
    assert miss.status_code == 404 and "gpt-4o" in miss.text
    dup = {"name": "dup", "models": [{"public_name": "a", "aliases": ["b"], "members": []}, {"public_name": "b", "members": []}]}
    assert client.post("/api/gateways", json=dup).status_code == 400
