"""Cache-alignment detector (TinyClaude/Headroom `cache_aligner`).

Finds volatile content in system prompts (UUIDs, timestamps, JWTs, hashes, API keys) that changes every request and so
prevents provider prefix/KV-cache reuse. Detection only: nothing is ever rewritten.
Deviation: TinyClaude stripped *all* punctuation inside a token (which broke timestamp and JWT detection); here only
surrounding punctuation is stripped.
"""
import hashlib
import re
from collections import Counter
from dataclasses import dataclass

LABEL_UUID, LABEL_ISO, LABEL_JWT, LABEL_HEX, LABEL_KEY = "uuid", "iso8601", "jwt", "hex_hash", "api_key"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_STRIP = ".,;:!?@#$%^&*()[]{}<>\"'`~"


@dataclass
class VolatileFinding:
    label: str
    sample: str


def _is_iso8601(t: str) -> bool:
    m = _ISO.match(t)
    if not m or len(t) < 8:
        return False
    mo, d = int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return False
    return m.group(4) is None or (int(m.group(4)) < 24 and int(m.group(5)) < 60)


def _is_jwt(t: str) -> bool:
    parts = t.split(".")
    return len(parts) == 3 and all(len(p) >= 4 and _B64URL.match(p) for p in parts)


def _is_api_key(t: str) -> bool:
    if re.match(r"^sk-[A-Za-z0-9_-]{12,}$", t) or re.match(r"^api_key_[A-Za-z0-9]{8,}$", t):
        return True
    if re.match(r"^[A-Za-z0-9_-]{20,}$", t):
        classes = sum(bool(re.search(p, t)) for p in ("[A-Z]", "[a-z]", "[0-9]"))
        return classes >= 2 and len(t) >= 24
    return False


def classify_token(t: str):
    if len(t) == 36 and _UUID.match(t):
        return LABEL_UUID
    if "." in t and _is_jwt(t):
        return LABEL_JWT
    if _is_iso8601(t):
        return LABEL_ISO
    if len(t) in (32, 40, 64) and _HEX.match(t):
        return LABEL_HEX
    if _is_api_key(t):
        return LABEL_KEY
    return None


def _truncate_sample(t: str) -> str:
    return t if len(t) <= 16 else t[:8] + "..." + t[-4:]  # never log full secrets


def detect_volatile_content(content: str) -> list[VolatileFinding]:
    out = []
    for raw in (content or "").split():
        tok = raw.strip(_STRIP)
        label = classify_token(tok) if tok else None
        if label:
            out.append(VolatileFinding(label, _truncate_sample(tok)))
    return out


def summarize_findings(findings: list[VolatileFinding]) -> dict:
    return dict(Counter(f.label for f in findings))


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def system_text(messages: list) -> list[str]:
    return [_text_of(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") in ("system", "developer")]


def compute_alignment_score(messages: list) -> int:
    """0-100: 100 minus 10 per volatile finding in system messages."""
    score = 100
    for txt in system_text(messages):
        score -= 10 * len(detect_volatile_content(txt))
    return max(0, min(100, score))


def compute_stable_prefix_hash(messages: list) -> str:
    return hashlib.sha1("\n---\n".join(system_text(messages)).encode("utf-8", "replace")).hexdigest()[:12]


def analyze_alignment(messages: list) -> dict:
    findings = [f for txt in system_text(messages) for f in detect_volatile_content(txt)]
    return {"score": max(0, 100 - 10 * len(findings)), "findings": len(findings),
            "labels": summarize_findings(findings), "prefix_hash": compute_stable_prefix_hash(messages)}
