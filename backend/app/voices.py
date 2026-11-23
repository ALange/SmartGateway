"""Parsing of an upstream's GET /audio/voices answer (shared by discovery and the gateway route)."""


def voice_id(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return str(v.get("id") or v.get("voice_id") or v.get("name") or "")
    return ""


def parse_voices(data) -> list:
    """Kokoro-FastAPI answers {"voices": [...]}; some servers answer a bare list. Items are strings or objects."""
    items = data.get("voices") if isinstance(data, dict) else data
    return [v for v in items if voice_id(v)][:500] if isinstance(items, list) else []


def clean_voices(voices) -> list[str]:
    """Trimmed, de-duplicated voice names chosen by an admin (at most 200 entries of 64 characters)."""
    out: list[str] = []
    for v in voices or []:
        v = str(v).strip()[:64]
        if v and v not in out:
            out.append(v)
    return out[:200]
