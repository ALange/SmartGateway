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
