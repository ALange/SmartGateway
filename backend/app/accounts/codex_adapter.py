"""OpenAI chat/completions <-> the ChatGPT Codex backend (Responses API, stream only).

request(): a chat/completions body -> a Responses body (instructions, input items, function tools, store=false, stream=true).
chunks():  the backend's SSE events -> chat.completion.chunk dicts; sse() renders them, collect() folds them into one chat.completion.
AdaptedResponse wraps the upstream httpx response so the rest of proxy._route keeps seeing a chat/completions upstream."""
import json
import time
import uuid
from typing import AsyncIterator, Optional

import httpx

from . import AccountError

DEFAULT_INSTRUCTIONS = "You are a helpful assistant."
# models a ChatGPT subscription usually offers; the account's own /models list replaces this when the backend answers
BUILTIN_MODELS = [
    {"id": "gpt-5.5", "context": 272_000},
    {"id": "gpt-5.4", "context": 272_000},
    {"id": "gpt-5.4-mini", "context": 272_000},
]
CAPS = ["chat", "tools", "vision", "reasoning"]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def _parts(content, out_type: str) -> list:
    if isinstance(content, str):
        return [{"type": out_type, "text": content}]
    parts = []
    for p in content or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            parts.append({"type": out_type, "text": p.get("text", "")})
        elif p.get("type") == "image_url" and out_type == "input_text":
            url = p.get("image_url")
            parts.append({"type": "input_image", "image_url": url.get("url") if isinstance(url, dict) else url})
    return parts


def request(chat: dict) -> dict:
    """The Responses body for a chat/completions body. Sampling limits (temperature, max_tokens ...) are not accepted by this backend."""
    instructions, items = [], []
    for m in chat.get("messages") or []:
        role = m.get("role")
        if role in ("system", "developer"):
            t = _text(m.get("content"))
            if t:
                instructions.append(t)
        elif role == "user":
            items.append({"type": "message", "role": "user", "content": _parts(m.get("content"), "input_text")})
        elif role == "assistant":
            t = _text(m.get("content"))
            if t:
                items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": t}]})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                items.append({"type": "function_call", "call_id": tc.get("id") or "call_" + uuid.uuid4().hex[:12], "name": fn.get("name", ""),
                              "arguments": fn.get("arguments") or "{}"})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id") or "", "output": _text(m.get("content"))})
    body: dict = {"model": chat.get("model"), "instructions": "\n\n".join(instructions) or DEFAULT_INSTRUCTIONS, "input": items,
                  "store": False, "stream": True, "parallel_tool_calls": bool(chat.get("parallel_tool_calls", True))}
    tools = [{"type": "function", "name": (t.get("function") or {}).get("name", ""), "description": (t.get("function") or {}).get("description", ""),
              "parameters": (t.get("function") or {}).get("parameters") or {"type": "object", "properties": {}}, "strict": False}
             for t in chat.get("tools") or [] if t.get("type") == "function"]
    if tools:
        body["tools"] = tools
        tc = chat.get("tool_choice")
        if tc in ("auto", "none", "required"):
            body["tool_choice"] = tc
        elif isinstance(tc, dict) and (tc.get("function") or {}).get("name"):
            body["tool_choice"] = {"type": "function", "name": tc["function"]["name"]}
    body["reasoning"] = {"effort": chat.get("reasoning_effort") or "medium", "summary": "auto"}
    rf = chat.get("response_format") or {}
    if rf.get("type") == "json_schema" and (rf.get("json_schema") or {}).get("schema"):
        js = rf["json_schema"]
        body["text"] = {"format": {"type": "json_schema", "name": js.get("name", "output"), "schema": js["schema"], "strict": bool(js.get("strict"))}}
    elif rf.get("type") == "json_object":
        body["text"] = {"format": {"type": "json_object"}}
    return body


def _usage(u: dict) -> dict:
    p, c = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    out = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": int(u.get("total_tokens") or p + c)}
    cached = (u.get("input_tokens_details") or {}).get("cached_tokens")
    if cached:
        out["prompt_tokens_details"] = {"cached_tokens": int(cached)}
    reasoning = (u.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning:
        out["completion_tokens_details"] = {"reasoning_tokens": int(reasoning)}
    return out


def _error_text(ev: dict) -> str:
    e = ev.get("error") or (ev.get("response") or {}).get("error") or {}
    return (e.get("message") if isinstance(e, dict) else str(e)) or ev.get("message") or "the model run failed"


async def events(lines: AsyncIterator[str]) -> AsyncIterator[dict]:
    async for line in lines:
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    yield json.loads(payload)
                except ValueError:
                    continue


async def chunks(lines: AsyncIterator[str], model: str) -> AsyncIterator[dict]:
    """chat.completion.chunk dicts for a Responses event stream. The last chunk carries finish_reason, then one with usage."""
    cid, created = "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time())
    tools: dict = {}            # output item id -> tool call index
    usage: dict = {}
    finish: Optional[str] = None
    started = False

    def mk(delta: dict, fin=None, **extra) -> dict:
        return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": fin}], **extra}

    async for ev in events(lines):
        t = ev.get("type", "")
        if not started and t != "error":
            started = True
            yield mk({"role": "assistant", "content": ""})
        if t == "response.output_text.delta" and ev.get("delta"):
            yield mk({"content": ev["delta"]})
        elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta") and ev.get("delta"):
            yield mk({"reasoning_content": ev["delta"]})
        elif t == "response.output_item.added" and (ev.get("item") or {}).get("type") == "function_call":
            it = ev["item"]
            idx = tools[it.get("id") or it.get("call_id")] = len(tools)
            yield mk({"tool_calls": [{"index": idx, "id": it.get("call_id") or it.get("id"), "type": "function",
                                      "function": {"name": it.get("name", ""), "arguments": ""}}]})
        elif t == "response.function_call_arguments.delta" and ev.get("delta") and ev.get("item_id") in tools:
            yield mk({"tool_calls": [{"index": tools[ev["item_id"]], "function": {"arguments": ev["delta"]}}]})
        elif t in ("response.completed", "response.incomplete"):
            r = ev.get("response") or {}
            usage = _usage(r.get("usage") or {})
            if t == "response.incomplete":
                finish = "content_filter" if (r.get("incomplete_details") or {}).get("reason") == "content_filter" else "length"
            else:
                finish = "tool_calls" if tools else "stop"
        elif t in ("response.failed", "error"):
            raise AccountError("The model run failed: " + _error_text(ev)[:300])
    if not started:
        yield mk({"role": "assistant", "content": ""})
    yield mk({}, finish or ("tool_calls" if tools else "stop"))
    if usage:
        yield {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [], "usage": usage}


def sse(chunk: dict) -> str:
    return "data: " + json.dumps(chunk, separators=(",", ":"))


async def collect(lines: AsyncIterator[str], model: str) -> dict:
    """Fold a chunk stream into one chat.completion (for clients that did not ask for a stream)."""
    text, think, calls, finish, usage, cid, created = [], [], {}, "stop", {}, None, int(time.time())
    async for ch in chunks(lines, model):
        cid, created = ch["id"], ch["created"]
        if ch.get("usage"):
            usage = ch["usage"]
        for c in ch.get("choices") or []:
            d = c.get("delta") or {}
            if d.get("content"):
                text.append(d["content"])
            if d.get("reasoning_content"):
                think.append(d["reasoning_content"])
            for tc in d.get("tool_calls") or []:
                cur = calls.setdefault(tc["index"], {"id": tc.get("id"), "type": "function", "function": {"name": "", "arguments": ""}})
                cur["id"] = tc.get("id") or cur["id"]
                fn = tc.get("function") or {}
                cur["function"]["name"] += fn.get("name") or ""
                cur["function"]["arguments"] += fn.get("arguments") or ""
            if c.get("finish_reason"):
                finish = c["finish_reason"]
    msg: dict = {"role": "assistant", "content": "".join(text) or (None if calls else "")}
    if think:
        msg["reasoning_content"] = "".join(think)
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return {"id": cid or "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _error_json(status: int, raw: bytes) -> bytes:
    """The vendor's error body as an OpenAI-style error (their shapes vary: {"detail": ...}, {"error": {...}})."""
    msg = ""
    try:
        d = json.loads(raw)
        e = d.get("error") if isinstance(d, dict) else None
        msg = (e.get("message") if isinstance(e, dict) else e) or (d.get("detail") if isinstance(d, dict) else "") or ""
        if isinstance(msg, (dict, list)):
            msg = json.dumps(msg)
    except ValueError:
        msg = raw.decode("utf-8", "replace")
    return json.dumps({"error": {"message": str(msg)[:400] or f"HTTP {status}", "type": "upstream_error", "code": status}}).encode()


class AdaptedResponse:
    """Looks like the httpx response of a chat/completions upstream (status_code, headers, aread, aiter_lines, aclose)."""

    def __init__(self, upstream: httpx.Response, model: str, stream: bool):
        self.up, self.model, self.stream = upstream, model, stream
        self.status_code = upstream.status_code
        ok = upstream.status_code < 400
        ctype = "text/event-stream" if ok and stream else "application/json"
        self.headers = httpx.Headers({"content-type": ctype})
        self._body: Optional[bytes] = None

    async def prepare(self) -> "AdaptedResponse":
        """A client that did not ask for a stream gets the whole answer: collect it now, so a failed run is a 502 (the gateway tries the next account)."""
        if not self.stream and self.status_code < 400:
            try:
                self._body = json.dumps(await collect(self._lines(), self.model)).encode()
            except AccountError as e:
                self.status_code = 502
                self._body = json.dumps({"error": {"message": str(e), "type": "upstream_error", "code": 502}}).encode()
            except Exception as e:  # noqa: BLE001 - connection dropped mid-answer
                self.status_code = 502
                self._body = json.dumps({"error": {"message": f"upstream read error: {type(e).__name__}", "type": "upstream_error", "code": 502}}).encode()
            await self.up.aclose()
        return self

    async def _lines(self):
        async for line in self.up.aiter_lines():
            yield line

    async def aread(self) -> bytes:
        if self._body is not None:
            return self._body
        if self.status_code >= 400:
            return _error_json(self.status_code, await self.up.aread())
        return json.dumps(await collect(self._lines(), self.model)).encode()

    async def aiter_lines(self):
        if self.status_code >= 400:
            yield (await self.aread()).decode()
            return
        try:
            async for ch in chunks(self._lines(), self.model):
                yield sse(ch)
                yield ""
        except AccountError as e:
            yield sse({"error": {"message": str(e), "type": "upstream_error", "code": 502}})
            yield ""
        yield "data: [DONE]"
        yield ""

    async def aclose(self):
        await self.up.aclose()
