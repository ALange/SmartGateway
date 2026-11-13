"""JSON compressor (Headroom "SmartCrusher" pattern, via TinyClaude).

Keeps structure (keys, brackets); samples long arrays; truncates long string values (never embedded JSON).
"""
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JSONCompressorConfig:
    max_items: int = 15  # 0 = keep every array item
    preserve_fields: list = field(default_factory=lambda: ["id", "name", "type", "status", "error", "message"])
    compression_ratio: float = 0.3
    preserve_keys: bool = True


@dataclass
class JSONCompressionResult:
    compressed: str
    original_items: int
    compressed_items: int
    strategy: str


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


class JSONCompressor:
    def __init__(self, config: JSONCompressorConfig | None = None):
        self.c = config or JSONCompressorConfig()

    def compress(self, content: str) -> JSONCompressionResult:
        try:
            parsed = json.loads(content)
        except ValueError:
            return JSONCompressionResult(content, 0, 0, "passthrough")
        if isinstance(parsed, list):
            return self._compress_array(parsed)
        if isinstance(parsed, dict):
            out = self._compress_object(parsed)
            return JSONCompressionResult(dumps(out), len(parsed), len(out), "object_preserve_keys")
        return JSONCompressionResult(dumps(parsed), 1, 1, "passthrough")

    def _compress_array(self, arr: list) -> JSONCompressionResult:
        total = len(arr)
        if self.c.max_items <= 0 or total <= self.c.max_items:
            return JSONCompressionResult(dumps(arr), total, total, "full")
        half = self.c.max_items // 2
        first, last = arr[:half], arr[-(self.c.max_items - half):]
        out = [*[self._value(x) for x in first], "...", *[self._value(x) for x in last]]
        return JSONCompressionResult(dumps(out), total, len(out), f"sampled_{self.c.max_items}")

    def _compress_object(self, obj: dict) -> dict:
        res: dict = {}
        for k, v in obj.items():
            if not self.c.preserve_keys:
                res[k] = v
            elif isinstance(v, list):
                res[k] = self._nested_array(v)
            elif isinstance(v, str):
                res[k] = v if k in self.c.preserve_fields else self._truncate(v)
            elif isinstance(v, dict):
                res[k] = self._compress_object(v)
            else:
                res[k] = v
        return res

    def _value(self, v: Any) -> Any:
        if isinstance(v, str):
            return self._truncate(v)
        if isinstance(v, list):
            return self._nested_array(v)
        if isinstance(v, dict):
            return self._compress_object(v)
        return v

    def _nested_array(self, arr: list) -> list:
        """Shared nested-array policy: first 3 + last 2 with a "..." marker, each element compressed recursively."""
        if len(arr) <= 3:
            return [self._value(x) for x in arr]
        return [*[self._value(x) for x in arr[:3]], "...", *[self._value(x) for x in arr[-2:]]]

    def _truncate(self, value: str) -> str:
        if len(value) <= 50 or self._is_embedded_json(value):
            return value
        target = max(30, int(len(value) * self.c.compression_ratio))
        keep_start = int(target * 0.6)
        keep_end = target - keep_start
        return value[:keep_start] + "\n...[compressed]...\n" + value[-keep_end:]

    @staticmethod
    def _is_embedded_json(value: str) -> bool:
        t = value.strip()
        if len(t) < 2 or not ((t[0] == "{" and t[-1] == "}") or (t[0] == "[" and t[-1] == "]")):
            return False
        try:
            json.loads(t)
            return True
        except ValueError:
            return False
