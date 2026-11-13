"""Pure aggregation math for the compression / context insights endpoints (no DB or HTTP here).

Contract (from TinyClaude): `tokens_saved`, `sum_ratio`, `ratio_count` of a group row come from FRESH events only
(cache_hit = 0). Cache-hit rows are re-recorded every time an already-compressed block is resent in a later turn, so
summing them would count one saving many times; they only feed `events` and `cache_hit_events`.
"""
from collections import defaultdict
from typing import Optional

CHARS_PER_TOKEN = 4
ESTIMATE_NOTE = ("Character-based estimates (~4 chars/token); not exact token counts. Only sizes and hashes are recorded, "
                 "never request content.")
MAX_GROWTH_SESSIONS = 20
MAX_POINTS_PER_SESSION = 500


def aggregate_compression_rows(rows: list[dict]) -> list[dict]:
    """rows: {key, events, cache_hit_events, tokens_saved, sum_ratio, ratio_count} -> sorted rows for the UI."""
    out = []
    for r in rows:
        key = r.get("key") or "Unknown"
        out.append({
            "key": key, "events": r["events"],
            "avgRatio": (r["sum_ratio"] / r["ratio_count"]) if r.get("ratio_count") else None,
            "tokensSaved": r["tokens_saved"],
            "cacheHitRate": (r["cache_hit_events"] / r["events"]) if r["events"] else 0.0,
        })
    out.sort(key=lambda x: (-x["tokensSaved"], x["key"]))
    return out


def build_compression_response(*, range_: str, requests: int, events: int, cache_hit_events: int, applied_events: int,
                               fresh_tokens_saved: int, sum_ratio: float, ratio_count: int, tokens_not_sent: int,
                               potential_not_sent: int, avg_alignment: Optional[float], by_content_type: list[dict],
                               by_compressor: list[dict], alignment: dict, live_cache: Optional[dict]) -> dict:
    return {
        "meta": {"range": range_},
        "totals": {
            "requests": requests, "compressionEventsCount": events, "appliedEvents": applied_events,
            "shadowEvents": events - applied_events,
            "cacheHitRate": (cache_hit_events / events) if events else 0.0,
            "avgCompressionRatio": (sum_ratio / ratio_count) if ratio_count else None,
            "uniqueTokensSaved": fresh_tokens_saved,       # each block counted once (first compression)
            "tokensNotSent": tokens_not_sent,               # every turn the smaller prompt was actually sent
            "potentialTokensNotSent": potential_not_sent,   # same, measured in shadow mode (not applied)
            "avgAlignmentScore": avg_alignment,
        },
        "byContentType": aggregate_compression_rows(by_content_type),
        "byCompressor": aggregate_compression_rows(by_compressor),
        "alignment": alignment,
        "liveCache": live_cache,
    }


def build_context_response(rows: list[dict], *, range_: str, top: int = 10, session_gap_minutes: int = 30, truncated: bool = False) -> dict:
    """rows: request rows (dicts) with ts, session_key, prompt_tokens, completion_tokens, ctx_* and ctx_blocks."""
    sys_c = sum(r.get("ctx_system_chars") or 0 for r in rows)
    tools_c = sum(r.get("ctx_tools_chars") or 0 for r in rows)
    msg_c = sum(r.get("ctx_messages_chars") or 0 for r in rows)
    total = sys_c + tools_c + msg_c
    n = len(rows)

    def share(x):
        return (x / total) if total else 0.0

    groups: dict[str, dict] = {}
    for r in rows:
        for b in r.get("ctx_blocks") or []:
            g = groups.setdefault(b["hash"], {"kind": b["kind"], "label": b["label"], "chars": b["chars"], "occurrences": 0, "requests": 0})
            g["occurrences"] += 1
            g["requests"] += 1
    contributors = sorted(({**g, "totalChars": g["chars"] * g["occurrences"]} for g in groups.values()), key=lambda g: -g["totalChars"])[:top]

    by_session: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("session_key"):
            by_session[r["session_key"]].append(r)
    sessions = []
    gap = session_gap_minutes * 60
    for key, rs in by_session.items():
        rs = sorted(rs, key=lambda r: r["ts"])
        cur = [rs[0]]
        chunks = []
        for r in rs[1:]:
            if r["ts"] - cur[-1]["ts"] > gap:
                chunks.append(cur)
                cur = []
            cur.append(r)
        chunks.append(cur)
        for i, ch in enumerate(chunks):
            pts = [{"ts": r["ts"], "promptTokens": r.get("prompt_tokens") or 0, "completionTokens": r.get("completion_tokens") or 0,
                    "ctxChars": (r.get("ctx_system_chars") or 0) + (r.get("ctx_tools_chars") or 0) + (r.get("ctx_messages_chars") or 0)}
                   for r in ch][-MAX_POINTS_PER_SESSION:]
            sessions.append({"sessionKey": f"{key[:14]}#{i + 1}" if len(chunks) > 1 else key[:14], "start": ch[0]["ts"], "end": ch[-1]["ts"],
                             "requests": len(ch), "points": pts})
    sessions.sort(key=lambda s: -s["end"])
    return {
        "meta": {"range": range_, "requestsAnalyzed": n, "truncated": truncated, "estimateNote": ESTIMATE_NOTE, "charsPerToken": CHARS_PER_TOKEN},
        "composition": {"requests": n, "systemChars": sys_c, "toolsChars": tools_c, "messagesChars": msg_c, "totalChars": total,
                        "shares": {"system": share(sys_c), "tools": share(tools_c), "messages": share(msg_c)},
                        "avgPerRequest": {"system": sys_c // n if n else 0, "tools": tools_c // n if n else 0, "messages": msg_c // n if n else 0}},
        "tokenTotals": {"requests": n, "prompt": sum(r.get("prompt_tokens") or 0 for r in rows),
                        "completion": sum(r.get("completion_tokens") or 0 for r in rows)},
        "contributors": contributors,
        "growth": sessions[:MAX_GROWTH_SESSIONS],
    }
