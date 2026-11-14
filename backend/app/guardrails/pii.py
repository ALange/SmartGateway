"""PII / secret detection and redaction (regex + checksum based, no external dependencies)."""
import re
from dataclasses import dataclass
from typing import Callable, Optional

KINDS = ["email", "phone", "card", "iban", "ssn", "pesel", "ip", "secret"]
LABEL = {"email": "EMAIL", "phone": "PHONE", "card": "CARD", "iban": "IBAN", "ssn": "SSN", "pesel": "PESEL", "ip": "IP", "secret": "SECRET"}


def luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = re.sub(r"\s", "", s).upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1
    except ValueError:
        return False


def pesel_ok(s: str) -> bool:
    if len(s) != 11 or not s.isdigit():
        return False
    w = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    return (10 - sum(int(a) * b for a, b in zip(s, w)) % 10) % 10 == int(s[10])


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# (kind, regex, validator) - listed in priority order: on overlapping matches the earlier entry wins
_DETECTORS: list = [
    ("secret", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"), None),
    ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                          r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), None),
    ("iban", re.compile(r"\b[A-Za-z]{2}\d{2}(?: ?[A-Za-z0-9]{4}){2,7}(?: ?[A-Za-z0-9]{1,3})?\b"), iban_ok),
    ("card", re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])"), lambda s: luhn(_digits(s))),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), None),
    ("ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), None),
    ("pesel", re.compile(r"(?<!\d)\d{11}(?!\d)"), pesel_ok),
    ("ip", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), None),
    ("phone", re.compile(r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?)?\d{3}[ .-]\d{3}[ .-]?\d{2,4}(?!\w)"
                         r"|(?<![\w+])\+\d{1,3}(?:[ .-]?\d){8,12}(?!\w)"), lambda s: 9 <= len(_digits(s)) <= 15),
]


@dataclass
class Finding:
    kind: str
    start: int
    end: int


def scan(text: str, kinds) -> list:
    """Non-overlapping findings of the requested kinds, in text order."""
    if not text:
        return []
    want = set(kinds)
    taken: list = []
    found: list = []
    for kind, rx, check in _DETECTORS:
        if kind not in want:
            continue
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            if check and not check(m.group(0)):
                continue
            taken.append((s, e))
            found.append(Finding(kind, s, e))
    return sorted(found, key=lambda f: f.start)


class Redactor:
    """Replaces findings with stable placeholders: the same value always maps to the same [KIND_n] within one request."""

    def __init__(self):
        self._seen: dict = {}
        self._count: dict = {}

    def placeholder(self, kind: str, value: str) -> str:
        key = (kind, re.sub(r"[\s-]", "", value).lower())
        if key not in self._seen:
            self._count[kind] = self._count.get(kind, 0) + 1
            self._seen[key] = f"[{LABEL[kind]}_{self._count[kind]}]"
        return self._seen[key]

    def apply(self, text: str, findings: list) -> str:
        out, pos = [], 0
        for f in findings:
            out.append(text[pos:f.start])
            out.append(self.placeholder(f.kind, text[f.start:f.end]))
            pos = f.end
        out.append(text[pos:])
        return "".join(out)
