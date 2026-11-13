"""Context-composition analysis of a chat request (what fills the context window). Stores sizes and hashes only, never content."""
import hashlib
import json
from typing import Optional

MIN_BLOCK_CHARS = 512
MAX_BLOCKS = 8
PREVIEW = 80


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for p in content:
            if isinstance(p, dict):
                n += len(p.get("text", "")) if p.get("type") == "text" else len(json.dumps(p, ensure_ascii=False)) if p.get("type") != "image_url" else 3000
        return n
    return 0


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _hash(kind: str, label: str, chars: int, sample: str) -> str:
    return hashlib.sha1(f"{kind}\x1f{label}\x1f{chars}\x1f{sample[:256]}".encode("utf-8", "replace")).hexdigest()[:16]


def analyze_context(body: dict) -> dict:
    msgs = body.get("messages") or []
    tool_names: dict[str, str] = {}
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                tool_names[tc.get("id", "")] = (tc.get("function") or {}).get("name", "tool")
    system = tools = messages = 0
    blocks: list[dict] = []

    def add(kind: str, label: str, text: str, chars: int):
        if chars >= MIN_BLOCK_CHARS:
            blocks.append({"kind": kind, "label": label, "chars": chars, "hash": _hash(kind, label, chars, text)})

    for m in msgs:
        if not isinstance(m, dict):
            continue
        role, c = m.get("role"), m.get("content")
        n = _chars(c)
        txt = _text(c)
        preview = " ".join(txt.split())[:PREVIEW]
        if role in ("system", "developer"):
            system += n
            add("system", preview, txt, n)
            continue
        messages += n
        if role in ("tool", "function"):
            add("tool_result", tool_names.get(m.get("tool_call_id", ""), m.get("name") or "tool"), txt, n)
        elif role == "assistant":
            add("assistant", preview, txt, n)
            for tc in m.get("tool_calls") or []:
                args = (tc.get("function") or {}).get("arguments", "")
                messages += len(args)
                add("tool_use", (tc.get("function") or {}).get("name", "tool"), args, len(args))
        else:
            add("user", preview, txt, n)
    for k in ("tools", "functions"):
        if body.get(k):
            s = json.dumps(body[k], ensure_ascii=False)
            tools += len(s)
            add("tools", f"{len(body[k])} tool definitions", s, len(s))
    blocks.sort(key=lambda b: -b["chars"])
    return {"system_chars": system, "tools_chars": tools, "messages_chars": messages, "blocks": blocks[:MAX_BLOCKS]}
