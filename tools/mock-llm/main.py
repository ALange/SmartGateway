"""Tiny OpenAI-compatible mock upstream for testing SmartGateway.

MODELS="name:cap1+cap2,name2:cap"  caps: vision, reasoning, embedding, tools (chat is default)
SLOTS=2  concurrent generations (extra requests wait, like llama.cpp)
TOKENS=12 tokens per reply   TOKEN_DELAY=0.05 seconds/token
CTX=0  context window (tokens, ~4 chars/token); 0 = unlimited. Over-long prompts get a 400 "maximum context length".
EXPOSE_PROPS=1 serves llama.cpp-style /props (total_slots, n_ctx, chat_template_caps) and /slots
OPENCODE_MODE=zen|go  serve OpenCode-style /models metadata; "go" refuses chat requests without an x-opencode-session header
   /hits = request count per path, /last_headers = headers of the last chat request
"""
import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

NAME = os.environ.get("MOCK_NAME", "mock")
MODELS = {}
for part in os.environ.get("MODELS", "mock-chat").split(","):
    n, _, caps = part.partition(":")
    MODELS[n.strip()] = set(filter(None, caps.split("+")))
SLOTS = int(os.environ.get("SLOTS", "2"))
TOKENS = int(os.environ.get("TOKENS", "12"))
TOKEN_DELAY = float(os.environ.get("TOKEN_DELAY", "0.05"))
CTX = int(os.environ.get("CTX", "0"))
FAIL = {"on": False}
OPENCODE = os.environ.get("OPENCODE_MODE", "")
MOCK_KEY = os.environ.get("MOCK_KEY", "")  # when set, chat requests need "Authorization: Bearer <MOCK_KEY>"
HITS: dict = {}
LAST_HEADERS: dict = {}

app = FastAPI()
sem = asyncio.Semaphore(SLOTS)
state = {"abort_after": 0, "in_flight": 0, "peak": 0, "total": 0, "queued": 0, "external": 0, "last_prompt_chars": 0}


@app.middleware("http")
async def count_hits(request: Request, call_next):
    HITS[request.url.path] = HITS.get(request.url.path, 0) + 1
    if request.url.path.endswith("/chat/completions"):
        LAST_HEADERS.clear()
        LAST_HEADERS.update({k.lower(): v for k, v in request.headers.items()})
    return await call_next(request)


@app.get("/hits")
def hits():
    return HITS


@app.get("/last_headers")
def last_headers():
    return LAST_HEADERS


def bad(msg, code=400):
    return JSONResponse({"error": {"message": msg, "type": "invalid_request_error"}}, status_code=code)


def opencode_item(m, caps):
    return {"id": m, "object": "model", "owned_by": "opencode", "name": m, "type": "chat",
            "modalities": {"input": ["text"] + (["image/png", "image/jpeg"] if "vision" in caps else []), "output": ["text"]},
            "capabilities": {"tools": {"supported": "tools" in caps}, "reasoning": {"supported": "reasoning" in caps}},
            "limit": {"context": CTX or 128000, "input": (CTX or 128000) - 8000, "output": 8000},
            "cost": {"text": {"input": {"price": 0 if m.endswith("-free") else 1.5}, "output": {"price": 0 if m.endswith("-free") else 6}}},
            "api": {"chat-completions": {"base_url": "https://opencode.ai/inference/openai/v1"}}}


@app.get("/v1/models")
def models():
    if OPENCODE:
        return {"object": "list", "data": [opencode_item(m, c) for m, c in MODELS.items()]}
    out = []
    for m in MODELS:
        d = {"id": m, "object": "model", "owned_by": NAME}
        if CTX and os.environ.get("EXPOSE_META", "0") == "1":
            d["meta"] = {"n_ctx": CTX}
        out.append(d)
    return {"object": "list", "data": out}


@app.get("/props")
def props():
    if os.environ.get("EXPOSE_PROPS", "0") != "1":
        return JSONResponse({}, status_code=404)
    caps = any("tools" in c for c in MODELS.values())
    return {"total_slots": SLOTS, "default_generation_settings": {"n_ctx": CTX or 4096},
            "chat_template_caps": {"supports_tools": caps, "supports_tool_calls": caps}}


@app.get("/slots")
def slots():
    if os.environ.get("EXPOSE_PROPS", "0") != "1":
        return JSONResponse({}, status_code=404)
    busy = state["in_flight"] + state["external"]
    return [{"id": i, "is_processing": i < busy} for i in range(SLOTS)]


@app.post("/admin/external/{n}")
def set_external(n: int):
    """Pretend n slots are busy with traffic that did not come through the gateway."""
    state["external"] = max(0, min(SLOTS, n))
    return state


@app.get("/stats")
def stats():
    return {"name": NAME, "slots": SLOTS, **state}


LAST = {"messages": None}


@app.get("/last")
def last():
    """The messages of the most recent chat request (lets tests check exactly what the gateway forwarded)."""
    return LAST


@app.post("/stats/reset")
def reset():
    state.update(peak=0, total=0)
    return state


@app.post("/admin/stream_abort/{n}")
def set_abort(n: int):
    """Break streams (connection error) after n chunks; 0 disables."""
    state["abort_after"] = n
    return state


@app.post("/admin/fail/{on}")
def set_fail(on: int):
    FAIL["on"] = bool(on)
    return FAIL


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    b = await req.json()
    m = b.get("model")
    if m not in MODELS:
        return bad("model not found", 404)
    if "embedding" not in MODELS[m]:
        return bad("model does not support embeddings")
    inputs = b["input"] if isinstance(b["input"], list) else [b["input"]]
    return {"object": "list", "model": m, "usage": {"prompt_tokens": 3, "total_tokens": 3},
            "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]} for i, _ in enumerate(inputs)]}


def _prompt_chars(messages) -> int:
    n = 0
    for x in messages:
        c = x.get("content")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += sum(len(p.get("text", "")) for p in c if isinstance(p, dict))
    return n


@app.post("/v1/chat/completions")
async def chat(req: Request):
    b = await req.json()
    m = b.get("model")
    if MOCK_KEY and req.headers.get("authorization", "") != f"Bearer {MOCK_KEY}":
        return JSONResponse({"error": {"message": "invalid api key", "type": "authentication_error"}}, status_code=401)
    if OPENCODE == "go" and not req.headers.get("x-opencode-session"):
        return JSONResponse({"type": "error", "error": {"type": "MissingSessionID", "message": "Request is missing x-opencode-session"}}, status_code=400)
    if FAIL["on"]:
        return bad("mock failure", 500)
    if m not in MODELS:
        return bad("model not found", 404)
    caps = MODELS[m]
    if "embedding" in caps:
        return bad("embedding model cannot chat")
    has_image = any(isinstance(x.get("content"), list) and any(p.get("type") == "image_url" for p in x["content"])
                    for x in b.get("messages", []))
    if has_image and "vision" not in caps:
        return bad("images not supported by this model")
    if (b.get("tools") or b.get("functions")) and "tools" not in caps:
        return bad("tools are not supported by this model")
    chars = _prompt_chars(b.get("messages", []))
    state["last_prompt_chars"] = chars
    LAST["messages"] = b.get("messages")
    if CTX and chars / 4 + int(b.get("max_tokens") or 0) > CTX:
        return bad(f"This model's maximum context length is {CTX} tokens. However, your request has about {int(chars/4)} tokens.")
    max_tokens = int(b.get("max_tokens") or TOKENS)
    n = min(TOKENS, max_tokens)
    reasoning = "reasoning" in caps
    cid = "chatcmpl-" + uuid.uuid4().hex[:8]
    prompt_tokens = max(5, chars // 4)
    words = [f"{NAME}-tok{i} " for i in range(n)]
    tool_call = None
    if b.get("tools") and "tools" in caps:
        fn = b["tools"][0].get("function", {}).get("name", "fn")
        tool_call = [{"id": "call_1", "type": "function", "function": {"name": fn, "arguments": "{}"}}]

    async def run_slot():
        state["queued"] += 1
        await sem.acquire()
        state["queued"] -= 1
        state["in_flight"] += 1
        state["total"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])

    def done():
        state["in_flight"] -= 1
        sem.release()

    if not b.get("stream"):
        await run_slot()
        try:
            await asyncio.sleep(TOKEN_DELAY * n)
        finally:
            done()
        msg = {"role": "assistant", "content": "".join(words)}
        if reasoning:
            msg["reasoning_content"] = "Let me think..."
        fin = "stop"
        if tool_call:
            msg["content"], msg["tool_calls"], fin = None, tool_call, "tool_calls"
        return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": m,
                "choices": [{"index": 0, "message": msg, "finish_reason": fin}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": n, "total_tokens": prompt_tokens + n}}

    async def gen():
        await run_slot()
        sent = 0
        try:
            def chunk(delta, fin=None):
                return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                                              "model": m, "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}) + "\n\n"

            async def out(c):
                nonlocal sent
                sent += 1
                if state["abort_after"] and sent > state["abort_after"]:
                    raise RuntimeError("mock: stream aborted")
                return c

            yield await out(chunk({"role": "assistant"}))
            if reasoning:
                yield await out(chunk({"reasoning_content": "Let me think..."}))
            if tool_call:
                fn = tool_call[0]["function"]["name"]
                yield await out(chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": fn, "arguments": ""}}]}))
                for piece in ['{"city": ', '"Paris", ', '"unit": ', '"celsius"}']:
                    await asyncio.sleep(TOKEN_DELAY)
                    yield await out(chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}))
                yield await out(chunk({}, "tool_calls"))
            else:
                for w in words:
                    await asyncio.sleep(TOKEN_DELAY)
                    yield await out(chunk({"content": w}))
                yield await out(chunk({}, "stop"))
            yield "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "model": m, "choices": [],
                                         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": n,
                                                   "total_tokens": prompt_tokens + n}}) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            done()

    return StreamingResponse(gen(), media_type="text/event-stream")
