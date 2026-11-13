"""OpenAI Responses API <-> chat completions translation (text, images, function tools)."""
import json
import time
import uuid
from typing import AsyncIterator


def to_chat(body: dict) -> dict:
    msgs: list = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": body["instructions"]})
    inp = body.get("input")
    if isinstance(inp, str):
        msgs.append({"role": "user", "content": inp})
    else:
        for it in inp or []:
            t = it.get("type", "message")
            if t == "message" or "role" in it:
                role = it.get("role", "user")
                if role == "developer":
                    role = "system"
                c = it.get("content")
                if isinstance(c, str):
                    msgs.append({"role": role, "content": c})
                else:
                    parts = []
                    for p in c or []:
                        pt = p.get("type")
                        if pt in ("input_text", "output_text", "text"):
                            parts.append({"type": "text", "text": p.get("text", "")})
                        elif pt == "input_image":
                            url = p.get("image_url")
                            url = url.get("url") if isinstance(url, dict) else url
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                    if all(x["type"] == "text" for x in parts):
                        msgs.append({"role": role, "content": "".join(x["text"] for x in parts)})
                    else:
                        msgs.append({"role": role, "content": parts})
            elif t == "function_call":
                msgs.append({"role": "assistant", "content": None, "tool_calls": [{
                    "id": it.get("call_id"), "type": "function",
                    "function": {"name": it.get("name"), "arguments": it.get("arguments", "{}")}}]})
            elif t == "function_call_output":
                msgs.append({"role": "tool", "tool_call_id": it.get("call_id"), "content": it.get("output", "")})
    out: dict = {"model": body.get("model"), "messages": msgs, "stream": bool(body.get("stream"))}
    if body.get("max_output_tokens"):
        out["max_tokens"] = body["max_output_tokens"]
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    if (body.get("reasoning") or {}).get("effort"):
        out["reasoning_effort"] = body["reasoning"]["effort"]
    tools = [t for t in body.get("tools") or [] if t.get("type") == "function"]
    if tools:
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("parameters", {"type": "object"})}} for t in tools]
    return out


def from_chat(resp: dict, public_model: str, req: dict) -> dict:
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    output = []
    if msg.get("content") or not msg.get("tool_calls"):
        output.append({"type": "message", "id": "msg_" + uuid.uuid4().hex[:16], "status": "completed", "role": "assistant",
                       "content": [{"type": "output_text", "text": msg.get("content") or "", "annotations": []}]})
    for tc in msg.get("tool_calls") or []:
        output.append({"type": "function_call", "id": "fc_" + uuid.uuid4().hex[:16], "call_id": tc.get("id"), "status": "completed",
                       "name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"})
    u = resp.get("usage") or {}
    return {"id": "resp_" + uuid.uuid4().hex[:20], "object": "response", "created_at": int(time.time()), "status": "completed",
            "model": public_model, "output": output,
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                      "total_tokens": u.get("total_tokens", 0)}}


def error_body(message: str, typ: str = "api_error") -> dict:
    return {"error": {"message": message, "type": typ, "code": typ}}


def _sse(data: dict) -> str:
    return f"event: {data['type']}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_from_chat(lines: AsyncIterator[str], public_model: str) -> AsyncIterator[str]:
    rid, mid = "resp_" + uuid.uuid4().hex[:20], "msg_" + uuid.uuid4().hex[:16]
    seq = 0

    def ev(d):
        nonlocal seq
        d["sequence_number"] = seq
        seq += 1
        return _sse(d)

    base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": public_model, "status": "in_progress", "output": []}
    yield ev({"type": "response.created", "response": base})
    started, text, usage = False, "", {}
    async for line in lines:
        line = line.strip()
        if not line.startswith("data:") or "[DONE]" in line:
            continue
        try:
            j = json.loads(line[5:])
        except Exception:  # noqa: BLE001
            continue
        if j.get("usage"):
            usage = j["usage"]
        ch = (j.get("choices") or [None])[0]
        d = (ch or {}).get("delta") or {}
        if d.get("content"):
            if not started:
                started = True
                yield ev({"type": "response.output_item.added", "output_index": 0,
                          "item": {"type": "message", "id": mid, "status": "in_progress", "role": "assistant", "content": []}})
                yield ev({"type": "response.content_part.added", "item_id": mid, "output_index": 0, "content_index": 0,
                          "part": {"type": "output_text", "text": "", "annotations": []}})
            text += d["content"]
            yield ev({"type": "response.output_text.delta", "item_id": mid, "output_index": 0, "content_index": 0, "delta": d["content"]})
    if not started:
        yield ev({"type": "response.output_item.added", "output_index": 0,
                  "item": {"type": "message", "id": mid, "status": "in_progress", "role": "assistant", "content": []}})
    part = {"type": "output_text", "text": text, "annotations": []}
    yield ev({"type": "response.output_text.done", "item_id": mid, "output_index": 0, "content_index": 0, "text": text})
    yield ev({"type": "response.content_part.done", "item_id": mid, "output_index": 0, "content_index": 0, "part": part})
    item = {"type": "message", "id": mid, "status": "completed", "role": "assistant", "content": [part]}
    yield ev({"type": "response.output_item.done", "output_index": 0, "item": item})
    done = {**base, "status": "completed", "output": [item],
            "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0)}}
    yield ev({"type": "response.completed", "response": done})
