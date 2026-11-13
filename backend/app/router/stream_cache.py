"""Streaming support for the response cache.

A stream is cached as the same aggregated `chat.completion` object that non-streaming requests produce, so one entry
serves both modes:
  * StreamAggregator rebuilds that object from the chunks of a live stream (only stored if the stream finished cleanly);
  * synthesize_stream turns a stored object back into OpenAI-style SSE lines (instantly, or paced like the original).
"""
import asyncio
import json
import re
import time
import uuid
from typing import AsyncIterator, Optional

META_KEY = "_sg"  # optional metadata stored beside the response (ttft_ms, gen_ms = original generation time, origin)
PIECE_CHARS = 20
MAX_PIECE_CHARS = 64
REPLAY_CAP_SECONDS = 10.0
FALLBACK_TPS = 50.0  # when an entry has no timing metadata


def split_meta(entry: dict) -> tuple[dict, dict]:
    """(response without metadata, metadata). The input is not modified."""
    return {k: v for k, v in entry.items() if k != META_KEY}, dict(entry.get(META_KEY) or {})


class StreamAggregator:
    """Accumulates the chunks of one chat completion stream (choice 0 only)."""

    def __init__(self, max_bytes: int = 512 * 1024):
        self.max_bytes = max_bytes
        self.size = 0
        self.id: Optional[str] = None
        self.created: Optional[int] = None
        self.fingerprint: Optional[str] = None
        self.role = "assistant"
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.reasoning_key: Optional[str] = None
        self.tools: dict[int, dict] = {}
        self.finish_reason: Optional[str] = None
        self.usage: Optional[dict] = None
        self.multi_choice = False
        self.has_logprobs = False
        self.oversize = False
        self.invalid = False

    def _add(self, n: int) -> None:
        self.size += n
        if self.size > self.max_bytes:
            self.oversize = True

    def feed(self, obj: dict) -> None:
        try:
            if not isinstance(obj, dict):
                return
            self.id = self.id or obj.get("id")
            self.created = self.created or obj.get("created")
            self.fingerprint = self.fingerprint or obj.get("system_fingerprint")
            if obj.get("usage"):
                self.usage = obj["usage"]
            for ch in obj.get("choices") or []:
                if (ch.get("index") or 0) != 0:
                    self.multi_choice = True
                    continue
                if ch.get("logprobs"):
                    self.has_logprobs = True
                d = ch.get("delta") or {}
                if d.get("role"):
                    self.role = d["role"]
                if d.get("content"):
                    self.content.append(d["content"])
                    self._add(len(d["content"]))
                for key in ("reasoning_content", "reasoning"):
                    if d.get(key):
                        self.reasoning_key = self.reasoning_key or key
                        self.reasoning.append(d[key])
                        self._add(len(d[key]))
                for tc in d.get("tool_calls") or []:
                    e = self.tools.setdefault(tc.get("index", 0) or 0, {"id": None, "type": "function", "name": "", "args": []})
                    if tc.get("id"):
                        e["id"] = tc["id"]
                    if tc.get("type"):
                        e["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name") and not e["name"]:
                        e["name"] = fn["name"]
                    if fn.get("arguments"):
                        e["args"].append(fn["arguments"])
                        self._add(len(fn["arguments"]))
                if ch.get("finish_reason"):
                    self.finish_reason = ch["finish_reason"]
        except Exception:  # noqa: BLE001 - caching must never break a live stream
            self.invalid = True

    @property
    def complete(self) -> bool:
        return bool(self.finish_reason) and not (self.multi_choice or self.has_logprobs or self.oversize or self.invalid)

    def result(self, public_model: str, fallback_usage: Optional[dict] = None) -> dict:
        msg: dict = {"role": self.role, "content": "".join(self.content) if (self.content or not self.tools) else None}
        if self.reasoning:
            msg[self.reasoning_key or "reasoning_content"] = "".join(self.reasoning)
        if self.tools:
            msg["tool_calls"] = [{"id": e["id"] or f"call_{i}", "type": e["type"],
                                  "function": {"name": e["name"], "arguments": "".join(e["args"])}}
                                 for i, e in sorted(self.tools.items())]
        out = {"id": self.id or "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion",
               "created": self.created or int(time.time()), "model": public_model,
               "choices": [{"index": 0, "message": msg, "finish_reason": self.finish_reason}],
               "usage": self.usage or fallback_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        if self.fingerprint:
            out["system_fingerprint"] = self.fingerprint
        return out


def pieces(text: str, size: int = PIECE_CHARS) -> list[str]:
    """Split on word boundaries into ~size-char pieces; ''.join(result) == text exactly."""
    out: list[str] = []
    cur = ""
    for tok in re.findall(r"\s*\S+|\s+", text or ""):
        if len(tok) > MAX_PIECE_CHARS:  # very long unbroken token (base64, minified JSON): hard split
            if cur:
                out.append(cur)
                cur = ""
            out.extend(tok[i:i + MAX_PIECE_CHARS] for i in range(0, len(tok), MAX_PIECE_CHARS))
        elif cur and len(cur) + len(tok) > size:
            out.append(cur)
            cur = tok
        else:
            cur += tok
    if cur:
        out.append(cur)
    return out


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n\n"


def build_chunks(resp: dict) -> list[tuple[str, int]]:
    """[(sse_line, weight)] reproducing `resp` as a stream (weight = characters, used to spread the replay time). Last item: `[DONE]`."""
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    base = {"id": resp.get("id"), "object": "chat.completion.chunk", "created": resp.get("created"), "model": resp.get("model")}
    if resp.get("system_fingerprint"):
        base["system_fingerprint"] = resp["system_fingerprint"]

    def chunk(delta: dict, finish=None) -> str:
        return _sse({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

    out: list[tuple[str, int]] = [(chunk({"role": msg.get("role") or "assistant", "content": ""}), 0)]
    for key in ("reasoning_content", "reasoning"):
        for p in pieces(msg.get(key) or ""):
            out.append((chunk({key: p}), len(p)))
    for p in pieces(msg.get("content") or ""):
        out.append((chunk({"content": p}), len(p)))
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        out.append((chunk({"tool_calls": [{"index": i, "id": tc.get("id"), "type": tc.get("type", "function"),
                                           "function": {"name": fn.get("name", ""), "arguments": ""}}]}), 4))
        for p in pieces(fn.get("arguments") or ""):
            out.append((chunk({"tool_calls": [{"index": i, "function": {"arguments": p}}]}), len(p)))
    out.append((chunk({}, ch.get("finish_reason") or "stop"), 0))
    out.append((_sse({**base, "choices": [], "usage": resp.get("usage") or {}}), 0))
    out.append(("data: [DONE]\n\n", 0))
    return out


def replay_delays(chunks: list[tuple[str, int]], meta: dict, speed: str) -> list[float]:
    """Seconds to wait before each chunk. Instant mode: all zeros. Realistic: the original generation time (capped) spread by size."""
    if speed != "realistic":
        return [0.0] * len(chunks)
    weights = [w for _, w in chunks]
    total_w = sum(weights)
    if not total_w:
        return [0.0] * len(chunks)
    gen_s = float(meta["gen_ms"]) / 1000.0 if meta.get("gen_ms") else (total_w / 4.0) / FALLBACK_TPS
    gen_s = min(REPLAY_CAP_SECONDS, max(0.0, gen_s))
    return [gen_s * w / total_w for w in weights]


async def synthesize_stream(resp: dict, speed: str = "instant", meta: Optional[dict] = None) -> AsyncIterator[str]:
    chunks = build_chunks(resp)
    for (line, _), delay in zip(chunks, replay_delays(chunks, meta or {}, speed)):
        if delay > 0.001:
            await asyncio.sleep(delay)
        yield line
