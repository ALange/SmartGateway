"""Heuristic prompt-injection detection. Pattern based: it flags well-known attack phrasing, it is not a guarantee."""
import re

_I = re.IGNORECASE
# (rule name, regex, weight)
RULES: list = [
    ("ignore_instructions", re.compile(r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,30}\b(?:previous|prior|above|earlier|preceding|all|any|your)\b[^.\n]{0,20}\b(?:instructions?|prompts?|rules|messages|guidelines|directives)\b", _I), 3),
    ("forget_everything", re.compile(r"\bforget (?:everything|all) (?:you|above|previous|that)\b", _I), 2),
    ("reveal_system_prompt", re.compile(r"\b(?:reveal|show|print|repeat|output|leak|display|tell me)\b[^.\n]{0,20}\b(?:your|the)\s+(?:system|hidden|initial|original|secret)\s+(?:prompt|instructions?|message)\b", _I), 3),
    ("role_override", re.compile(r"\byou are now\b[^.\n]{0,25}\b(?:dan|jailbroken|unrestricted|unfiltered|developer mode|evil)\b|\bdeveloper mode (?:enabled|on|activated)\b|\bact as\b[^.\n]{0,15}\b(?:unfiltered|unrestricted|jailbroken)\b|\bDAN mode\b", _I), 3),
    ("fake_delimiters", re.compile(r"<\|(?:im_start|im_end|system|endoftext)\|>|\[/?INST\]|</?system>|^\s*(?:#{1,3}\s*)?system\s*:", _I | re.MULTILINE), 2),
    ("hidden_instruction", re.compile(r"\b(?:do not|don't|never) (?:tell|inform|let|mention (?:this )?to) the user\b|\bnew instructions\s*:|\bfrom now on,? (?:you must|you will|ignore)\b", _I), 2),
    ("exfiltration", re.compile(r"\b(?:send|post|upload|forward|exfiltrate)\b[^.\n]{0,40}\b(?:conversation|chat history|api[ _-]?keys?|credentials|secrets?|system prompt)\b[^.\n]{0,40}\bhttps?://", _I), 3),
]
THRESHOLD = {"low": 3, "medium": 2, "high": 1}


def scan(text: str) -> list:
    """Matched rules as (name, weight)."""
    if not text:
        return []
    return [(name, w) for name, rx, w in RULES if rx.search(text)]


def flagged(hits: list, sensitivity: str) -> bool:
    return sum(w for _, w in hits) >= THRESHOLD.get(sensitivity, 2)
