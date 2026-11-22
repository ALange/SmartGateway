"""OpenAI Responses API <-> chat completions translation.

Covers what agent clients such as Codex CLI send: instructions/developer messages, text and images, function tools,
freeform ("custom") tools such as apply_patch, parallel tool calls, reasoning items, tool_choice, structured output, and
a complete event stream (reasoning, text, tool calls, response.completed / incomplete / failed)."""
import json
import time
import uuid
from typing import AsyncIterator

from ..runtime_ctx import verbose


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:16]


def custom_tools(req: dict) -> set:
    """Names of freeform tools; the upstream sees them as functions with a single string argument `input`."""
    return {t.get("name") for t in (req or {}).get("tools") or [] if t.get("type") == "custom"}


def _out_text(o) -> str:
    if o is None:
        return ""
    if isinstance(o, str):
        return o
    if isinstance(o, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in o)
    if isinstance(o, dict) and isinstance(o.get("content"), str):
        return o["content"]
    return json.dumps(o)


def _tool_call(call_id, name, args: str) -> dict:
    return {"id": call_id or _id("call_"), "type": "function", "function": {"name": name, "arguments": args or "{}"}}


def _add_call(msgs: list, call: dict):
    """Parallel tool calls arrive as consecutive items; chat templates want them in one assistant message."""
    last = msgs[-1] if msgs else None
    if last and last["role"] == "assistant" and last.get("tool_calls") is not None:
        last["tool_calls"].append(call)
    elif last and last["role"] == "assistant" and "tool_calls" not in last:
        last["tool_calls"] = [call]
    else:
        msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})


def to_chat(body: dict) -> dict:
    msgs: list = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": body["instructions"]})
    inp = body.get("input")
    customs = custom_tools(body)
    if isinstance(inp, str):
        msgs.append({"role": "user", "content": inp})
    else:
        for it in inp or []:
            t = it.get("type", "message")
            if t == "message" or ("role" in it and t not in ("function_call", "custom_tool_call")):
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
                _add_call(msgs, _tool_call(it.get("call_id"), it.get("name"), it.get("arguments")))
            elif t == "custom_tool_call":
                _add_call(msgs, _tool_call(it.get("call_id"), it.get("name"), json.dumps({"input": it.get("input", "")})))
            elif t in ("function_call_output", "custom_tool_call_output"):
                msgs.append({"role": "tool", "tool_call_id": it.get("call_id"), "content": _out_text(it.get("output"))})
            # reasoning, local_shell_call, web_search_call, item_reference ... have no chat equivalent and are dropped
    out: dict = {"model": body.get("model"), "messages": msgs, "stream": bool(body.get("stream"))}
    if body.get("max_output_tokens"):
        out["max_tokens"] = body["max_output_tokens"]
    for k in ("temperature", "top_p", "parallel_tool_calls"):
        if k in body and body[k] is not None:
            out[k] = body[k]
    if (body.get("reasoning") or {}).get("effort"):
        out["reasoning_effort"] = body["reasoning"]["effort"]
    tools = []
    for t in body.get("tools") or []:
        if t.get("type") == "function":
            tools.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("parameters") or {"type": "object", "properties": {}}}})
        elif t.get("type") == "custom":
            tools.append({"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": {"type": "object", "required": ["input"],
                                                                          "properties": {"input": {"type": "string", "description": "The complete freeform input for this tool."}}}}})
    if tools:
        out["tools"] = tools
        tc = body.get("tool_choice")
        if isinstance(tc, str) and tc in ("auto", "none", "required"):
            out["tool_choice"] = tc
        elif isinstance(tc, dict) and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    fmt = (body.get("text") or {}).get("format") or {}
    if fmt.get("type") == "json_schema" and fmt.get("schema"):
        out["response_format"] = {"type": "json_schema", "json_schema": {"name": fmt.get("name", "output"), "schema": fmt["schema"], "strict": bool(fmt.get("strict"))}}
    elif fmt.get("type") == "json_object":
        out["response_format"] = {"type": "json_object"}
    return out


def _usage(u: dict) -> dict:
    return {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
            "total_tokens": u.get("total_tokens") or u.get("prompt_tokens", 0) + u.get("completion_tokens", 0),
            "input_tokens_details": {"cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)},
            "output_tokens_details": {"reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)}}


def _reasoning_item(text: str, iid: str = "") -> dict:
    return {"type": "reasoning", "id": iid or _id("rs_"), "summary": [], "content": [{"type": "reasoning_text", "text": text}]}


def _call_item(call_id: str, name: str, args: str, customs: set, iid: str = "") -> dict:
    if name in customs:
        try:
            inp = json.loads(args or "{}").get("input", "")
        except Exception:  # noqa: BLE001 the model sent the raw text instead of {"input": ...}
            inp = args or ""
        return {"type": "custom_tool_call", "id": iid or _id("ctc_"), "call_id": call_id, "status": "completed", "name": name, "input": inp}
    return {"type": "function_call", "id": iid or _id("fc_"), "call_id": call_id, "status": "completed", "name": name, "arguments": args or "{}"}


def from_chat(resp: dict, public_model: str, req: dict) -> dict:
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    customs = custom_tools(req)
    output = []
    think = msg.get("reasoning_content") or msg.get("reasoning")
    if think:
        output.append(_reasoning_item(think))
    if msg.get("content") or not msg.get("tool_calls"):
        output.append({"type": "message", "id": _id("msg_"), "status": "completed", "role": "assistant",
                       "content": [{"type": "output_text", "text": msg.get("content") or "", "annotations": []}]})
    for tc in msg.get("tool_calls") or []:
        output.append(_call_item(tc.get("id") or _id("call_"), tc["function"]["name"], tc["function"].get("arguments") or "{}", customs))
    cut = ch.get("finish_reason") == "length"
    out = {"id": "resp_" + uuid.uuid4().hex[:20], "object": "response", "created_at": int(time.time()), "status": "incomplete" if cut else "completed",
           "model": public_model, "output": output, "usage": _usage(resp.get("usage") or {})}
    if cut:
        out["incomplete_details"] = {"reason": "max_output_tokens"}
    return out


def error_body(message: str, typ: str = "api_error") -> dict:
    return {"error": {"message": message, "type": typ, "code": typ}}


def _sse(data: dict) -> str:
    return f"event: {data['type']}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_from_chat(lines: AsyncIterator[str], public_model: str, req: dict = None) -> AsyncIterator[str]:
    rid = "resp_" + uuid.uuid4().hex[:20]
    customs = custom_tools(req)
    seq = 0

    def ev(d):
        nonlocal seq
        d["sequence_number"] = seq
        seq += 1
        return _sse(d)

    base = {"id": rid, "object": "response", "created_at": int(time.time()), "model": public_model, "status": "in_progress", "output": []}
    yield ev({"type": "response.created", "response": base})
    yield ev({"type": "response.in_progress", "response": base})
    items: list = []                 # finished output items, in order
    cur = None                       # the open item: {"kind", "index", "id", "text"/..., }
    tool_map: dict = {}              # upstream tool index -> open tool item
    usage, finish = {}, None

    def close():
        """Finish the open item; returns its events."""
        nonlocal cur
        if cur is None:
            return []
        c, out = cur, []
        cur = None
        if c["kind"] == "text":
            part = {"type": "output_text", "text": c["text"], "annotations": []}
            out.append(ev({"type": "response.output_text.done", "item_id": c["id"], "output_index": c["index"], "content_index": 0, "text": c["text"]}))
            out.append(ev({"type": "response.content_part.done", "item_id": c["id"], "output_index": c["index"], "content_index": 0, "part": part}))
            item = {"type": "message", "id": c["id"], "status": "completed", "role": "assistant", "content": [part]}
        elif c["kind"] == "reasoning":
            out.append(ev({"type": "response.reasoning_text.done", "item_id": c["id"], "output_index": c["index"], "content_index": 0, "text": c["text"]}))
            item = _reasoning_item(c["text"], c["id"])
        else:
            if c["name"] in customs:
                try:
                    inp = json.loads(c["text"] or "{}").get("input", "")
                except Exception:  # noqa: BLE001
                    inp = c["text"]
                out.append(ev({"type": "response.custom_tool_call_input.done", "item_id": c["id"], "output_index": c["index"], "input": inp}))
            else:
                out.append(ev({"type": "response.function_call_arguments.done", "item_id": c["id"], "output_index": c["index"], "arguments": c["text"] or "{}"}))
            item = _call_item(c["call_id"], c["name"], c["text"], customs, c["id"])
        items.append(item)
        out.append(ev({"type": "response.output_item.done", "output_index": c["index"], "item": item}))
        return out

    def open_item(kind, **extra):
        nonlocal cur
        cur = {"kind": kind, "index": len(items), "id": _id({"text": "msg_", "reasoning": "rs_"}.get(kind, "fc_")), "text": "", **extra}
        if kind == "text":
            item = {"type": "message", "id": cur["id"], "status": "in_progress", "role": "assistant", "content": []}
        elif kind == "reasoning":
            item = {"type": "reasoning", "id": cur["id"], "summary": []}
        elif cur["name"] in customs:
            item = {"type": "custom_tool_call", "id": cur["id"], "call_id": cur["call_id"], "status": "in_progress", "name": cur["name"], "input": ""}
        else:
            item = {"type": "function_call", "id": cur["id"], "call_id": cur["call_id"], "status": "in_progress", "name": cur["name"], "arguments": ""}
        out = [ev({"type": "response.output_item.added", "output_index": cur["index"], "item": item})]
        if kind == "text":
            out.append(ev({"type": "response.content_part.added", "item_id": cur["id"], "output_index": cur["index"], "content_index": 0,
                           "part": {"type": "output_text", "text": "", "annotations": []}}))
        return out

    failed = None
    try:
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
            think = d.get("reasoning_content") or d.get("reasoning")
            if think:
                if cur is None or cur["kind"] != "reasoning":
                    for e in close():
                        yield e
                    for e in open_item("reasoning"):
                        yield e
                cur["text"] += think
                yield ev({"type": "response.reasoning_text.delta", "item_id": cur["id"], "output_index": cur["index"], "content_index": 0, "delta": think})
            if d.get("content"):
                if cur is None or cur["kind"] != "text":
                    for e in close():
                        yield e
                    for e in open_item("text"):
                        yield e
                cur["text"] += d["content"]
                yield ev({"type": "response.output_text.delta", "item_id": cur["id"], "output_index": cur["index"], "content_index": 0, "delta": d["content"]})
            for tc in d.get("tool_calls") or []:
                ti, fn = tc.get("index", 0), tc.get("function") or {}
                if ti not in tool_map:
                    for e in close():
                        yield e
                    for e in open_item("tool", call_id=tc.get("id") or _id("call_"), name=fn.get("name", "")):
                        yield e
                    tool_map[ti] = cur
                tgt = tool_map[ti]
                if fn.get("arguments"):
                    tgt["text"] += fn["arguments"]
                    if tgt["name"] not in customs:   # a freeform input is only meaningful once its JSON wrapper is complete: sent in *.done
                        yield ev({"type": "response.function_call_arguments.delta", "item_id": tgt["id"], "output_index": tgt["index"], "delta": fn["arguments"]})
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    except Exception as e:  # noqa: BLE001
        failed = str(e) if verbose.get() else "The upstream model server failed while answering."
    for e in close():
        yield e
    if not items and failed is None:   # an empty answer still needs one message item
        for e in open_item("text"):
            yield e
        for e in close():
            yield e
    if failed is not None:
        yield ev({"type": "response.failed", "response": {**base, "status": "failed", "output": items,
                                                          "error": {"code": "upstream_error", "message": f"Upstream stream failed: {failed}"}}})
        return
    cut = finish == "length"
    done = {**base, "status": "incomplete" if cut else "completed", "output": items, "usage": _usage(usage)}
    if cut:
        done["incomplete_details"] = {"reason": "max_output_tokens"}
    yield ev({"type": "response.incomplete" if cut else "response.completed", "response": done})
