"""Anthropic Messages API <-> OpenAI chat completions translation."""
import asyncio
import json
import time
import uuid
from typing import AsyncIterator

STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "content_filter": "end_turn"}


def _text_of(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    return "".join(b.get("text", "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "text")


def to_chat(body: dict, strict: bool = False) -> dict:
    """strict (gateway "Anthropic compatible" switch) also honours thinking, tool_choice none, top_k and merges same-role turns."""
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
    if strict:
        msgs = _merge_turns(msgs)
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
        elif strict and tc.get("type") == "none":
            out["tool_choice"] = "none"
        if strict and tc.get("disable_parallel_tool_use"):
            out["parallel_tool_calls"] = False
    if strict:
        if body.get("top_k") is not None:
            out["top_k"] = body["top_k"]
        th = body.get("thinking") or {}
        if th.get("type") == "enabled":
            b = int(th.get("budget_tokens") or 0)
            out["reasoning_effort"] = "low" if b and b < 4096 else "high" if b >= 16384 else "medium"
        elif th.get("type") == "disabled":
            out["reasoning_effort"] = "none"
    return out


def _merge_turns(msgs: list) -> list:
    """Anthropic allows consecutive turns of one role; many chat templates do not. Join plain-text neighbours."""
    out: list = []
    for m in msgs:
        p = out[-1] if out else None
        if (p and p["role"] == m["role"] and m["role"] in ("user", "assistant") and not p.get("tool_calls") and not m.get("tool_calls")
                and isinstance(p.get("content"), str) and isinstance(m.get("content"), str)):
            p["content"] = p["content"] + "\n\n" + m["content"]
        else:
            out.append(dict(m))
    return out


def from_chat(resp: dict, public_model: str, strict: bool = False) -> dict:
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = []
    think = (msg.get("reasoning_content") or msg.get("reasoning")) if strict else None
    if think:
        content.append({"type": "thinking", "thinking": think, "signature": ""})
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
    stop = STOP_MAP.get(ch.get("finish_reason") or "stop", "end_turn")
    if strict and ch.get("finish_reason") == "content_filter":
        stop = "refusal"
    return {"id": "msg_" + uuid.uuid4().hex[:20], "type": "message", "role": "assistant", "model": public_model,
            "content": content or [{"type": "text", "text": ""}], "stop_reason": stop, "stop_sequence": None,
            "usage": _usage(u, strict)}


def _usage(u: dict, strict: bool, input_tokens: int = 0) -> dict:
    out = {"input_tokens": u.get("prompt_tokens") or input_tokens, "output_tokens": u.get("completion_tokens", 0)}
    if strict:
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        out.update({"cache_creation_input_tokens": 0, "cache_read_input_tokens": cached})
    return out


STATUS_TYPES = {400: "invalid_request_error", 401: "authentication_error", 402: "billing_error", 403: "permission_error",
                404: "not_found_error", 413: "request_too_large", 429: "rate_limit_error", 529: "overloaded_error"}


def error_body(message: str, typ: str = "api_error", status: int = 0, request_id: str = "") -> dict:
    """With a status (strict mode) the Anthropic error type follows the HTTP status exactly."""
    t = {"invalid_request_error": "invalid_request_error", "invalid_api_key": "authentication_error",
         "rate_limit_exceeded": "rate_limit_error", "not_found": "not_found_error", "model_not_found": "not_found_error",
         "guardrail_blocked": "invalid_request_error", "output_validation_failed": "api_error"}.get(typ, "api_error")
    if status:
        t = STATUS_TYPES.get(status) or ("api_error" if status >= 500 else t)
    out = {"type": "error", "error": {"type": t, "message": message}}
    if request_id:
        out["request_id"] = request_id
    return out


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


PING_EVERY = 10.0


async def _with_pings(lines: AsyncIterator[str], every: float):
    """Yield the upstream lines, or None whenever `every` seconds pass without one (so a ping can be sent)."""
    it = lines.__aiter__()
    task = asyncio.ensure_future(it.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=every)
            if not done:
                yield None
                continue
            try:
                item = task.result()
            except StopAsyncIteration:
                return
            yield item
            task = asyncio.ensure_future(it.__anext__())
    finally:
        if not task.done():
            task.cancel()
        aclose = getattr(it, "aclose", None)
        if aclose:
            await aclose()


async def stream_from_chat(lines: AsyncIterator[str], public_model: str, strict: bool = False, input_tokens: int = 0) -> AsyncIterator[str]:
    """Convert OpenAI SSE lines into Anthropic SSE events.
    strict adds what the Anthropic SDK / Claude Code expect: input tokens in message_start, ping events, thinking blocks and
    an `error` event when the upstream stream breaks."""
    mid = "msg_" + uuid.uuid4().hex[:20]
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": public_model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": _usage({}, strict, input_tokens) if strict else {"input_tokens": 0, "output_tokens": 0}}})
    if strict:
        yield _sse("ping", {"type": "ping"})
    idx, open_kind, tool_idx_map = -1, None, {}
    stop, usage = "end_turn", {}

    def close():
        nonlocal open_kind
        if open_kind is not None:
            sig = _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "signature_delta", "signature": ""}}) if open_kind == "thinking" else ""
            open_kind = None
            return sig + _sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        return ""

    source = _with_pings(lines, PING_EVERY) if strict else lines
    try:
        async for line in source:
            if line is None:
                yield _sse("ping", {"type": "ping"})
                continue
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
            think = (d.get("reasoning_content") or d.get("reasoning")) if strict else None
            if think:
                if open_kind != "thinking":
                    c = close()
                    if c:
                        yield c
                    idx += 1
                    open_kind = "thinking"
                    yield _sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "thinking_delta", "thinking": think}})
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
                if strict and ch["finish_reason"] == "content_filter":
                    stop = "refusal"
    except Exception as e:  # noqa: BLE001  strict: tell the client instead of cutting the stream
        if not strict:
            raise
        yield _sse("error", {"type": "error", "error": {"type": "api_error", "message": f"Upstream stream failed: {e}"}})
        return
    c = close()
    if c:
        yield c
    u = {"output_tokens": usage.get("completion_tokens", 0)}
    if strict:
        u = _usage(usage, True, input_tokens)
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": u})
    yield _sse("message_stop", {"type": "message_stop"})
