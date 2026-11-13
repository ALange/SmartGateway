"""Compression pipeline for chat requests: compress older tool results, leave everything else untouched.

Live zone: every message after the last assistant message (tool results the model has not seen yet) is never touched.
Only `tool`/`function` messages are compressed; user, assistant and system text never are.
"""
import math
from dataclasses import dataclass, field
from typing import Optional

from .cache import (CompressionCache, content_hash, extract_tool_result_contents, is_tool_result_message,
                    swap_tool_result_content_at)
from .router import ContentRouter, ContentRouterConfig

_cache = CompressionCache()
_routers: dict[str, ContentRouter] = {}
HEAVY_CHARS = 200_000  # above this, callers should run the pipeline in a worker thread


def get_cache() -> CompressionCache:
    return _cache


def get_router(config: Optional[dict]) -> ContentRouter:
    import json
    key = json.dumps(config or {}, sort_keys=True, default=str)
    r = _routers.get(key)
    if r is None:
        if len(_routers) > 16:
            _routers.clear()
        r = _routers[key] = ContentRouter(ContentRouterConfig.from_dict(config))
    return r


@dataclass
class PipelineResult:
    messages: list
    events: list = field(default_factory=list)
    mutated: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    applied: bool = False

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


def _tok(n: int) -> int:
    return math.ceil(n / 4)


def live_zone_start(messages: list) -> int:
    """Index of the first message of the live zone (everything after the last assistant message)."""
    last = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            last = i
    return last + 1 if last >= 0 else 0


def tool_chars(messages: list) -> int:
    n = 0
    for m in messages[: live_zone_start(messages)]:
        if is_tool_result_message(m):
            n += sum(len(t) for _, t in extract_tool_result_contents(m))
    return n


def apply_compression(messages: list, router: ContentRouter, mode: str = "on", cache: Optional[CompressionCache] = None) -> PipelineResult:
    """mode 'on': return compressed messages. 'shadow': measure only (messages returned unchanged). 'off': no-op."""
    cache = cache or _cache
    res = PipelineResult(messages=messages, applied=(mode == "on"))
    if mode == "off" or not isinstance(messages, list):
        return res
    end = live_zone_start(messages)
    if end <= 0:
        return res
    min_chars = router.c.min_content_chars
    out = list(messages)
    for i in range(end):
        msg = messages[i]
        if not is_tool_result_message(msg):
            continue
        cur = msg
        for idx, content in extract_tool_result_contents(msg):
            if not content:
                continue
            h = content_hash(content)
            cached = cache.get_compressed(h, len(content))
            if cached is not None:
                final, cache_hit, r = cached, True, None
                meta = cache.get_meta(h) or {}
            else:
                r = router.compress(content)
                final = r.compressed if len(r.compressed) < len(content) else content  # never keep a result that is not shorter
                cache.store_compressed(h, final, max(0, _tok(len(content)) - _tok(len(final))), len(content), {"content_type": r.content_type.value})
                cache_hit = False
            if len(content) < min_chars and final == content:
                continue  # too small to be interesting: no event, no noise in the metrics
            tb, ta = _tok(len(content)), _tok(len(final))
            res.tokens_before += tb
            res.tokens_after += ta
            res.events.append({
                "content_type": meta.get("content_type") if cache_hit else r.content_type.value,
                "compressor_used": "cache_hit" if cache_hit else (r.compressor_used if final != content else f"{r.compressor_used}(no_gain)"),
                "strategy": None if cache_hit else r.strategy,
                "compression_ratio": (len(final) / len(content)) if content else None,
                "tokens_before": tb, "tokens_after": ta, "chars_before": len(content), "chars_after": len(final),
                "cache_hit": cache_hit, "applied": mode == "on",
            })
            if mode == "on" and final != content:
                cur = swap_tool_result_content_at(cur, idx, final)
                res.mutated = True
        out[i] = cur
    if mode == "on" and res.mutated:
        res.messages = out
    return res
