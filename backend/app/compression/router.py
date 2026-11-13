"""Content router (TinyClaude/Headroom): detect the content type and dispatch to the best sub-compressor.

Mixed content (code fences, JSON blocks, search results) is split into sections that are compressed independently.
"""
import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

from .compressors.json_c import JSONCompressor, JSONCompressorConfig
from .compressors.log_c import LogCompressor, LogCompressorConfig
from .compressors.search_c import SearchCompressor, SearchCompressorConfig
from .compressors.simple_c import SimpleCompressor, SimpleCompressorConfig
from .detector import ContentType, DetectionResult, detect_content_type


@dataclass
class ContentRouterConfig:
    enable_json: bool = True
    enable_log: bool = True
    enable_search: bool = True
    enable_simple: bool = True
    json: JSONCompressorConfig = field(default_factory=JSONCompressorConfig)
    log: LogCompressorConfig = field(default_factory=LogCompressorConfig)
    search: SearchCompressorConfig = field(default_factory=SearchCompressorConfig)
    simple: SimpleCompressorConfig = field(default_factory=SimpleCompressorConfig)
    min_content_chars: int = 100
    enable_mixed_content: bool = True

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ContentRouterConfig":
        """Build from a (partial) plain dict, e.g. the saved settings. Unknown keys are ignored."""
        d = d or {}
        cfg = cls()
        for f in fields(cls):
            if f.name not in d:
                continue
            v = d[f.name]
            cur = getattr(cfg, f.name)
            if hasattr(cur, "__dataclass_fields__") and isinstance(v, dict):
                for sf in fields(cur):
                    if sf.name in v:
                        setattr(cur, sf.name, v[sf.name])
            elif not hasattr(cur, "__dataclass_fields__"):
                setattr(cfg, f.name, v)
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ContentRouterResult:
    compressed: str
    original: str
    content_type: ContentType
    detection_confidence: float
    compressor_used: str
    compression_ratio: float
    tokens_before: int
    tokens_after: int
    was_mixed: bool = False
    sub_results: list = field(default_factory=list)
    strategy: str = ""


def est_tokens(s: str) -> int:
    return math.ceil(len(s) / 4)


class ContentRouter:
    def __init__(self, config: Optional[ContentRouterConfig] = None):
        self.c = config or ContentRouterConfig()
        self.json = JSONCompressor(self.c.json)
        self.log = LogCompressor(self.c.log)
        self.search = SearchCompressor(self.c.search)
        self.simple = SimpleCompressor(self.c.simple)

    # -- main entry ---------------------------------------------------------
    def compress(self, content: str) -> ContentRouterResult:
        tokens_before = est_tokens(content)
        if not content or len(content) < self.c.min_content_chars:
            minified = self._try_minify_json(content)
            if minified is not None and minified != content:
                return ContentRouterResult(minified, content, ContentType.JSON, 1.0, "JSONMinifier", len(minified) / len(content),
                                           tokens_before, est_tokens(minified), strategy="json_minify_short")
            return self._passthrough(content, ContentType.UNKNOWN, 0.0, "too_short")
        if self.c.enable_mixed_content and self._is_mixed(content):
            return self._compress_mixed(content)
        det = detect_content_type(content)
        r = self._route(content, det)
        r.original, r.detection_confidence, r.content_type = content, det.confidence, det.content_type
        r.tokens_before, r.tokens_after, r.was_mixed = tokens_before, est_tokens(r.compressed), False
        return r

    # -- routing ------------------------------------------------------------
    def _route(self, content: str, det: DetectionResult) -> ContentRouterResult:
        def res(compressed, name, strategy, ctype=None):
            return ContentRouterResult(compressed, content, ctype or det.content_type, det.confidence, name,
                                       len(compressed) / max(1, len(content)), 0, 0, strategy=strategy)

        if det.content_type == ContentType.JSON and self.c.enable_json:
            jr = self.json.compress(content)
            return res(jr.compressed, "JSONCompressor", jr.strategy, ContentType.JSON)
        if det.content_type == ContentType.LOG and self.c.enable_log:
            lr = self.log.compress(content)
            return res(lr.compressed, "LogCompressor", lr.strategy, ContentType.LOG)
        if det.content_type == ContentType.CODE and self.c.enable_search:
            sr = self.search.compress(content)
            if sr.original_matches > 0:
                return res(sr.compressed, "SearchCompressor", sr.strategy, ContentType.CODE)
        if self.c.enable_simple:
            sc = self.simple.compress(content)
            return res(sc.compressed, "SimpleCompressor", sc.strategy)
        return self._passthrough(content, det.content_type, det.confidence, "no_compressor_enabled")

    # -- mixed content ------------------------------------------------------
    @staticmethod
    def _is_mixed(content: str) -> bool:
        flags = [bool(re.search(r"^```", content, re.M)), bool(re.search(r"^\s*[\[{]", content, re.M)), bool(re.search(r"^\S+:\d+:", content, re.M))]
        return sum(flags) >= 2

    def _compress_mixed(self, content: str) -> ContentRouterResult:
        subs, parts = [], []
        for sec in self._split(content):
            det = detect_content_type(sec)
            r = self._route(sec, det)
            parts.append(r.compressed)
            subs.append({"content_type": det.content_type.value, "compressor_used": r.compressor_used})
        out = "\n".join(parts)
        return ContentRouterResult(out, content, ContentType.TEXT, 0.5, "ContentRouter(mixed)", len(out) / max(1, len(content)),
                                   est_tokens(content), est_tokens(out), True, subs, "mixed_routing")

    @staticmethod
    def _split(content: str) -> list[str]:
        lines = content.split("\n")
        sections, cur = [], []
        for ln in lines:
            if re.match(r"^```", ln):
                if cur:
                    sections.append("\n".join(cur))
                    cur = []
            cur.append(ln)
        if cur:
            sections.append("\n".join(cur))
        if len(sections) <= 1:  # no fences: fall back to paragraph breaks
            paras, para = [], []
            for ln in lines:
                if ln.strip() == "":
                    if para:
                        paras.append("\n".join(para))
                        para = []
                else:
                    para.append(ln)
            if para:
                paras.append("\n".join(para))
            if len(paras) > 1:
                return paras
        return sections

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _passthrough(content: str, ctype: ContentType, conf: float, reason: str) -> ContentRouterResult:
        t = est_tokens(content)
        return ContentRouterResult(content, content, ctype, conf, "passthrough", 1.0, t, t, strategy=f"passthrough({reason})")

    @staticmethod
    def _try_minify_json(content: str) -> Optional[str]:
        t = (content or "").strip()
        if not t.startswith(("{", "[")):
            return None
        try:
            return json.dumps(json.loads(t), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            return None
