"""Fallback compressor: head/tail truncation with a marker (TinyClaude/Headroom `_simple_compress`).

Optional `preserve_identifiers` (off by default, not in TinyClaude): UUIDs/hashes/tokens that fall into the cut part are
appended in a short footer, using the entropy mask.
"""
from dataclasses import dataclass

from ..mask import compute_entropy_mask


@dataclass
class SimpleCompressorConfig:
    compression_ratio: float = 0.3
    min_chars: int = 100
    preserve_identifiers: bool = False
    max_identifiers: int = 20


@dataclass
class SimpleCompressionResult:
    compressed: str
    original_chars: int
    compressed_chars: int
    strategy: str


class SimpleCompressor:
    def __init__(self, config: SimpleCompressorConfig | None = None):
        self.c = config or SimpleCompressorConfig()

    def compress(self, content: str) -> SimpleCompressionResult:
        n = len(content)
        if not content or n < self.c.min_chars:
            return SimpleCompressionResult(content, n, n, "passthrough")
        target = max(50, int(n * self.c.compression_ratio))
        if n <= target:
            return SimpleCompressionResult(content, n, n, "passthrough")
        keep_start = int(target * 0.66)
        keep_end = target - keep_start
        out = content[:keep_start] + "\n...[compressed]...\n" + content[-keep_end:]
        if self.c.preserve_identifiers:
            cut = content[keep_start: n - keep_end]
            toks = [t.strip(".,;:()[]{}\"'`") for t in cut.split()]
            mask = compute_entropy_mask(toks)
            ids, seen = [], set(out.split())
            for t, keep in zip(toks, mask.mask):
                if keep and t not in seen and t not in ids:
                    ids.append(t)
                if len(ids) >= self.c.max_identifiers:
                    break
            if ids:
                out += "\n[ids in compressed part: " + ", ".join(ids) + "]"
        return SimpleCompressionResult(out, n, len(out), "truncation")
