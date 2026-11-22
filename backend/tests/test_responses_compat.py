import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db, proxy
from app.compat import responses as r
from app.main import app
from app.models import Endpoint, Model, RequestLog
from app.router import health


async def _lines(items):
    for i in items:
        yield i


def evs(out):
    return [json.loads(e.split("data: ")[1]) for e in out]


# ------------------------------------------------------------------ request translation (what Codex sends)
def test_codex_style_request():
    body = {"model": "m", "instructions": "be brief", "stream": True, "parallel_tool_calls": True, "tool_choice": "auto",
            "reasoning": {"effort": "medium", "summary": "auto"}, "store": False, "include": ["reasoning.encrypted_content"],
            "text": {"format": {"type": "json_schema", "name": "o", "schema": {"type": "object"}, "strict": True}},
            "tools": [{"type": "function", "name": "shell", "description": "run", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}},
                      {"type": "custom", "name": "apply_patch", "description": "edit files", "format": {"type": "grammar"}},
                      {"type": "web_search"}],
            "input": [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "rules"}]},
                      {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix it"}]},
                      {"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "x"},
                      {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": '{"cmd":"ls"}'},
                      {"type": "function_call", "call_id": "c2", "name": "shell", "arguments": '{"cmd":"pwd"}'},
                      {"type": "function_call_output", "call_id": "c1", "output": "a.py"},
                      {"type": "function_call_output", "call_id": "c2", "output": {"content": "/w", "success": True}},
                      {"type": "custom_tool_call", "call_id": "c3", "name": "apply_patch", "input": "*** Begin Patch"},
                      {"type": "custom_tool_call_output", "call_id": "c3", "output": "ok"}]}
    c = r.to_chat(body)
    roles = [m["role"] for m in c["messages"]]
    assert roles == ["system", "system", "user", "assistant", "tool", "tool", "assistant", "tool"]   # reasoning dropped, parallel calls merged
    assert [t["id"] for t in c["messages"][3]["tool_calls"]] == ["c1", "c2"]
    assert c["messages"][5]["content"] == "/w"
    assert json.loads(c["messages"][6]["tool_calls"][0]["function"]["arguments"]) == {"input": "*** Begin Patch"}
    assert [t["function"]["name"] for t in c["tools"]] == ["shell", "apply_patch"]       # web_search has no chat equivalent
    assert c["tools"][1]["function"]["parameters"]["required"] == ["input"]
    assert c["tool_choice"] == "auto" and c["parallel_tool_calls"] is True and c["reasoning_effort"] == "medium"
    assert c["response_format"]["json_schema"]["name"] == "o"
    assert r.to_chat({"model": "m", "input": "x", "tools": [{"type": "function", "name": "f"}], "tool_choice": {"type": "function", "name": "f"}})["tool_choice"]["function"]["name"] == "f"


def test_response_with_tool_calls_reasoning_and_truncation():
    req = {"tools": [{"type": "custom", "name": "apply_patch"}]}
    resp = {"choices": [{"message": {"content": "", "reasoning_content": "hmm", "tool_calls": [
        {"id": "a", "function": {"name": "shell", "arguments": '{"cmd":"ls"}'}},
        {"id": "b", "function": {"name": "apply_patch", "arguments": '{"input":"PATCH"}'}}]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    o = r.from_chat(resp, "pub", req)
    assert [i["type"] for i in o["output"]] == ["reasoning", "function_call", "custom_tool_call"] and o["output"][2]["input"] == "PATCH"
    assert o["usage"]["total_tokens"] == 12 and o["usage"]["input_tokens_details"]["cached_tokens"] == 0 and o["status"] == "completed"
    cut = r.from_chat({"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}, "pub", {})
    assert cut["status"] == "incomplete" and cut["incomplete_details"]["reason"] == "max_output_tokens"


# ------------------------------------------------------------------ streaming
async def test_stream_text_and_parallel_tool_calls():
    chunks = ['data: {"choices":[{"delta":{"reasoning_content":"think"}}]}', 'data: {"choices":[{"delta":{"content":"Let me look."}}]}',
              'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"shell","arguments":"{\\"cmd\\""}}]}}]}',
              'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":\\"ls\\"}"}}]}}]}',
              'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"c2","function":{"name":"apply_patch","arguments":"{\\"input\\":\\"P\\"}"}}]}}]}',
              'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
              'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":6}}', "data: [DONE]"]
    out = evs([e async for e in r.stream_from_chat(_lines(chunks), "pub", {"tools": [{"type": "custom", "name": "apply_patch"}]})])
    types = [e["type"] for e in out]
    assert types[0] == "response.created" and types[-1] == "response.completed"
    assert [e["sequence_number"] for e in out] == list(range(len(out)))
    done = out[-1]["response"]
    assert [i["type"] for i in done["output"]] == ["reasoning", "message", "function_call", "custom_tool_call"]
    assert done["output"][1]["content"][0]["text"] == "Let me look."
    assert done["output"][2]["arguments"] == '{"cmd":"ls"}' and done["output"][2]["call_id"] == "c1" and done["output"][3]["input"] == "P"
    assert done["usage"]["input_tokens"] == 4 and done["usage"]["total_tokens"] == 10
    assert types.count("response.function_call_arguments.delta") == 2 and "response.custom_tool_call_input.done" in types
    assert [e["output_index"] for e in out if e["type"] == "response.output_item.done"] == [0, 1, 2, 3]


async def test_stream_empty_truncated_and_failed():
    out = evs([e async for e in r.stream_from_chat(_lines(["data: [DONE]"]), "pub")])
    assert out[-1]["type"] == "response.completed" and out[-1]["response"]["output"][0]["type"] == "message"
    cut = evs([e async for e in r.stream_from_chat(_lines(['data: {"choices":[{"delta":{"content":"a"},"finish_reason":"length"}]}']), "pub")])
    assert cut[-1]["type"] == "response.incomplete" and cut[-1]["response"]["incomplete_details"]["reason"] == "max_output_tokens"

    async def broken():
        yield 'data: {"choices":[{"delta":{"content":"a"}}]}'
        raise RuntimeError("boom")
    bad = evs([e async for e in r.stream_from_chat(broken(), "pub")])
    assert bad[-1]["type"] == "response.failed" and "boom" not in bad[-1]["response"]["error"]["message"]


# ------------------------------------------------------------------ through a gateway
@pytest.fixture(scope="module")
def client():
    db.init_db()
    yield TestClient(app)
    with Session(db.engine) as s:
        for row in s.exec(select(RequestLog)).all():
            s.delete(row)
        s.commit()


def test_responses_route_end_to_end(client, monkeypatch):
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "shell" and body["messages"][-1]["role"] == "tool"
        parts = [{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "z", "function": {"name": "shell", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
                 {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}]
        sse = "".join(f"data: {json.dumps(p)}\n\n" for p in parts) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with Session(db.engine) as s:
        ep = Endpoint(name=f"rc-{time.time_ns()}", base_url="http://fake/v1", status="online")
        s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="up", capabilities=["chat"], max_slots=4, context_length=32000)
        s.add(m); s.commit(); s.refresh(m)
        ep_id, m_id = ep.id, m.id
    health.status[ep_id] = "online"
    gw = client.post("/api/gateways", json={"name": f"rc-{time.time_ns()}", "models": [{"public_name": "codex-m", "members": [{"model_id": m_id}]}]}).json()
    res = client.post(f"/api/admin-gw/{gw['slug']}/v1/responses", json={
        "model": "codex-m", "stream": True, "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
        "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
                  {"type": "function_call", "call_id": "p", "name": "shell", "arguments": "{}"}, {"type": "function_call_output", "call_id": "p", "output": "done"}]})
    assert res.status_code == 200, res.text
    events = [json.loads(l[6:]) for l in res.text.split("\n") if l.startswith("data: ")]
    assert events[-1]["type"] == "response.completed" and events[-1]["response"]["output"][0]["type"] == "function_call"
