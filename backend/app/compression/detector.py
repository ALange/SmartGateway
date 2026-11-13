"""Content type detection — ported from TinyClaude (itself ported from Headroom's detector).

Heuristic/structural, no ML: fast and dependency-free. Used by the ContentRouter to pick a sub-compressor.
"""
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    JSON = "json"
    CODE = "code"
    LOG = "log"
    DIFF = "diff"
    MARKDOWN = "markdown"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class DetectionResult:
    content_type: ContentType
    confidence: float
    label: str
    language: Optional[str] = None


CODE_INDICATORS = ["def ", "class ", "function ", "import ", "const ", "let ", "var ", "func ", "fn ", "pub ",
                   "package ", "impl ", "trait ", "enum ", "interface ", "extends ", "// ", "/*", "*/", "=>", "->", "```"]
LOG_INDICATORS = ["ERROR", "WARN", "INFO", "DEBUG", "FATAL", "TRACE", "error:", "warning:", "Error:", "Warning:"]
DIFF_INDICATORS = ["diff --git", "--- ", "+++ ", "@@ -", "@@ +"]
SEARCH_RESULT_PATTERN = re.compile(r"^\S+:\d+:", re.M)
MARKDOWN_LINK = re.compile(r"\[.+\]\(.+\)")

LANG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("python", re.compile(r"\bdef \w+\s*\(|class \w+.*:|import \w+|from \w+ import")),
    ("javascript", re.compile(r"\bconst |\blet |\bvar |\bfunction\b|=>|console\.")),
    ("typescript", re.compile(r":\s*(string|number|boolean|any|void)\b|interface \w+|type \w+=")),
    ("go", re.compile(r"\bfunc \w+|package \w+|import \(")),
    ("rust", re.compile(r"\bfn \w+|let mut|use \w+::|impl \w+")),
    ("java", re.compile(r"public class|private \w+|protected \w+|import java\.")),
    ("c", re.compile(r"#include <|int main\(|void \w+\(")),
    ("cpp", re.compile(r"#include <|std::|template|class \w+ \{")),
    ("ruby", re.compile(r"\bdef \w+|end\b|require '|attr_")),
    ("shell", re.compile(r"#!")),
]


def _guess_language(content: str) -> Optional[str]:
    for lang, pat in LANG_PATTERNS:
        if pat.search(content):
            return lang
    return None


def detect_content_type(content: str) -> DetectionResult:
    if not content or not content.strip():
        return DetectionResult(ContentType.UNKNOWN, 0.0, "empty")
    trimmed = content.strip()

    if trimmed.startswith(("{", "[")):
        try:
            json.loads(trimmed)
            return DetectionResult(ContentType.JSON, 1.0, "json")
        except ValueError:
            pass

    if any(ind in trimmed for ind in DIFF_INDICATORS):
        return DetectionResult(ContentType.DIFF, 0.9, "diff")

    if SEARCH_RESULT_PATTERN.search(trimmed):
        return DetectionResult(ContentType.CODE, 0.7, "search_result")  # search results are code-like

    lines = trimmed.split("\n")[:20]
    if sum(1 for ln in lines if any(ind in ln for ind in LOG_INDICATORS)) >= 3:
        return DetectionResult(ContentType.LOG, 0.8, "log")

    if any(ind in trimmed for ind in CODE_INDICATORS):
        return DetectionResult(ContentType.CODE, 0.7, "code", _guess_language(trimmed))

    if trimmed.startswith("#") or "## " in trimmed or MARKDOWN_LINK.search(trimmed):
        return DetectionResult(ContentType.MARKDOWN, 0.6, "markdown")

    return DetectionResult(ContentType.TEXT, 0.5, "text")


def detect_batch(contents: list[str]) -> list[DetectionResult]:
    return [detect_content_type(c) for c in contents]
