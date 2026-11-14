"""Anthropic Messages API <-> OpenAI chat completions translation."""
import json
import time
import uuid
from typing import AsyncIterator

STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "content_filter": "end_turn"}


def _text_of(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    return "".join(b.get("text", "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "text")


def to_chat(body: dict) -> dict:
    msgs: list = []
    system = body.get("system")
    if system:
        msgs.append({"role": "system", "content": _text_of(system)})
    for m in body.get("messages", []):
        role, content = m.get("role"), m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        parts, tool_calls = [], []
        for b in content or []:
            t = b.get("type")
            if t == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif t == "image":
                src = b.get("source", {})
                url = src.get("url") if src.get("type") == "url" else f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif t == "tool_use":
                tool_calls.append({"id": b.get("id"), "type": "function",
                                   "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input", {}))}})
            elif t == "tool_result":
                c = b.get("content")
                msgs.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                             "content": c if isinstance(c, str) else _text_of(c)})
        if tool_calls:
            msgs.append({"role": "assistant", "content": _text_of(parts) or None, "tool_calls": tool_calls})
        elif parts:
            only_text = all(p["type"] == "text" for p in parts)
            msgs.append({"role": role, "content": "".join(p["text"] for p in parts) if only_text else parts})
    out: dict = {"model": body.get("model"), "messages": msgs, "stream": bool(body.get("stream"))}
    if body.get("max_tokens"):
        out["max_tokens"] = body["max_tokens"]
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    if body.get("tools"):
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("input_schema", {"type": "object"})}}
                        for t in body["tools"]]
        tc = body.get("tool_choice") or {}
        if tc.get("type") == "any":
            out["tool_choice"] = "required"
        elif tc.get("type") == "tool":
            out["tool_choice"] = {"type": "function", "function": {"name": tc.get("name")}}
        elif tc.get("type") == "auto":
            out["tool_choice"] = "auto"
    return out


def from_chat(resp: dict, public_model: str) -> dict:
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except Exception:  # noqa: BLE001
            args = {}
        content.append({"type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:12],
                        "name": tc["function"]["name"], "input": args})
    u = resp.get("usage") or {}
    return {"id": "msg_" + uuid.uuid4().hex[:20], "type": "message", "role": "assistant", "model": public_model,
            "content": content or [{"type": "text", "text": ""}],
            "stop_reason": STOP_MAP.get(ch.get("finish_reason") or "stop", "end_turn"), "stop_sequence": None,
            "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def error_body(message: str, typ: str = "api_error") -> dict:
    t = {"invalid_request_error": "invalid_request_error", "invalid_api_key": "authentication_error",
         "rate_limit_exceeded": "rate_limit_error", "not_found": "not_found_error", "model_not_found": "not_found_error",
         "guardrail_blocked": "invalid_request_error", "output_validation_failed": "api_error"}.get(typ, "api_error")
    return {"type": "error", "error": {"type": t, "message": message}}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_from_chat(lines: AsyncIterator[str], public_model: str) -> AsyncIterator[str]:
    """Convert OpenAI SSE lines into Anthropic SSE events."""
    mid = "msg_" + uuid.uuid4().hex[:20]
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": public_model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
    idx, open_kind, tool_idx_map = -1, None, {}
    stop, usage = "end_turn", {}

    def close():
        nonlocal open_kind
        if open_kind is not None:
            open_kind = None
            return _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        return ""

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
        if not ch:
            continue
        d = ch.get("delta") or {}
        if d.get("content"):
            if open_kind != "text":
                c = close()
                if c:
                    yield c
                idx += 1
                open_kind = "text"
                yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": d["content"]}})
        for tc in d.get("tool_calls") or []:
            ti = tc.get("index", 0)
            fn = tc.get("function") or {}
            if ti not in tool_idx_map:
                c = close()
                if c:
                    yield c
                idx += 1
                tool_idx_map[ti] = idx
                open_kind = "tool"
                yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {
                    "type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:12], "name": fn.get("name", ""), "input": {}}})
            if fn.get("arguments"):
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": tool_idx_map[ti],
                                                   "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})
        if ch.get("finish_reason"):
            stop = STOP_MAP.get(ch["finish_reason"], "end_turn")
    c = close()
    if c:
        yield c
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": usage.get("completion_tokens", 0)}})
    yield _sse("message_stop", {"type": "message_stop"})
