"""Content-addressed compression cache with LRU eviction (TinyClaude/Headroom `compression_cache`).

Maps the hash of an original tool result to its compressed form so later turns resending the same history reuse it
(and, being deterministic, keep the prompt prefix byte-identical across turns).
Helpers work on OpenAI chat messages (`role: "tool"` / legacy `"function"`; content is a string or a list of text parts).
"""
import copy
import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

TOOL_ROLES = ("tool", "function")


def is_tool_result_message(msg: dict) -> bool:
    return isinstance(msg, dict) and msg.get("role") in TOOL_ROLES


def extract_tool_result_contents(msg: dict) -> list[tuple[int, str]]:
    """[(part_index, text)] — part_index is -1 for plain string content."""
    c = msg.get("content")
    if isinstance(c, str):
        return [(-1, c)]
    out = []
    if isinstance(c, list):
        for i, part in enumerate(c):
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                out.append((i, part["text"]))
    return out


def swap_tool_result_content_at(msg: dict, index: int, new: str) -> dict:
    m = copy.deepcopy(msg)
    if index == -1:
        m["content"] = new
    elif isinstance(m.get("content"), list) and 0 <= index < len(m["content"]):
        m["content"][index]["text"] = new
    return m


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()[:32]


@dataclass
class _Entry:
    compressed: str
    tokens_saved: int
    original_length: int  # -1 = unguarded
    meta: dict | None = None  # e.g. content_type of the original, so cache hits can be attributed


class CompressionCache:
    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        self._d: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0
        self._tokens_saved = 0

    def get_compressed(self, h: str, expected_original_length: Optional[int] = None) -> Optional[str]:
        with self._lock:
            e = self._d.get(h)
            if e is None:
                self.misses += 1
                return None
            if expected_original_length is not None and e.original_length != -1 and e.original_length != expected_original_length:
                self.misses += 1  # residual hash-collision guard; entry is kept
                return None
            self.hits += 1
            self._d.move_to_end(h)
            return e.compressed

    def store_compressed(self, h: str, compressed: str, tokens_saved: int, original_length: Optional[int] = None, meta: Optional[dict] = None) -> None:
        with self._lock:
            old = self._d.pop(h, None)
            if old:
                self._tokens_saved -= old.tokens_saved
            self._d[h] = _Entry(compressed, tokens_saved, -1 if original_length is None else original_length, meta)
            self._tokens_saved += tokens_saved
            while len(self._d) > self.max_entries:
                _, ev = self._d.popitem(last=False)
                self._tokens_saved -= ev.tokens_saved

    def get_meta(self, h: str) -> Optional[dict]:
        with self._lock:
            e = self._d.get(h)
            return e.meta if e else None

    def clear(self) -> int:
        with self._lock:
            n = len(self._d)
            self._d.clear()
            self._tokens_saved = 0
            return n

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {"entries": len(self._d), "hits": self.hits, "misses": self.misses,
                    "hit_rate": (self.hits / total) if total else None, "tokens_saved": self._tokens_saved, "max_entries": self.max_entries}

    @staticmethod
    def content_hash(content: str) -> str:
        return content_hash(content)
