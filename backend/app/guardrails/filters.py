"""User-defined content filter rules (regex)."""
import re
from functools import lru_cache

from . import checks


@lru_cache(maxsize=256)
def compile_rule(pattern: str):
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def validate_rule(rule: dict) -> None:
    pat = rule.get("pattern")
    if not isinstance(pat, str) or not pat.strip():
        raise ValueError("filter rule needs a pattern")
    if len(pat) > 8000:
        raise ValueError("filter pattern is too long (max 8000 characters)")
    try:
        rx = compile_rule(pat)
    except re.error as e:
        raise ValueError(f"invalid filter regex '{pat}': {e}")
    if rx.match(""):
        raise ValueError(f"filter regex '{pat}' matches empty text")
    if rule.get("validator") and rule["validator"] not in checks.VALIDATORS:
        raise ValueError(f"unknown validation '{rule['validator']}'; known: {sorted(checks.VALIDATORS)}")
    if rule.get("stage", "both") not in ("input", "output", "both"):
        raise ValueError("filter stage must be input, output or both")
    if rule.get("action", "block") not in ("block", "redact"):
        raise ValueError("filter action must be block or redact")


def find(rule: dict, text: str) -> list:
    """Matches of the rule in text. With a validator, only structurally valid numbers count (e.g. a PESEL with a correct checksum).
    A pattern may name a group (?P<n>...) for the number itself, so that a label in front of it is not validated."""
    check = checks.get(rule.get("validator"))
    out = []
    for m in compile_rule(rule["pattern"]).finditer(text):
        if check and not check(m.groupdict().get("n") or m.group(0)):
            continue
        out.append(m)
    return out


def redact(rule: dict, text: str, repl: str = "[FILTERED]") -> str:
    out, pos = [], 0
    for m in find(rule, text):
        out.append(text[pos:m.start()])
        out.append(repl)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)
