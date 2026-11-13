"""Structure masks and entropy-based preservation (ported from TinyClaude/Headroom masks)."""
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class MaskSpan:
    start: int
    end: int
    is_structural: bool
    label: Optional[str] = None


@dataclass
class StructureMask:
    tokens: list
    mask: list  # True = preserve (structural), False = compressible
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if len(self.tokens) != len(self.mask):
            raise ValueError(f"Mask length ({len(self.mask)}) must match tokens length ({len(self.tokens)})")

    @property
    def preservation_ratio(self) -> float:
        return sum(self.mask) / len(self.mask) if self.mask else 0.0

    @property
    def structural_count(self) -> int:
        return sum(1 for m in self.mask if m)

    @property
    def compressible_count(self) -> int:
        return len(self.mask) - self.structural_count

    def structural_tokens(self) -> list:
        return [t for t, m in zip(self.tokens, self.mask) if m]

    def compressible_tokens(self) -> list:
        return [t for t, m in zip(self.tokens, self.mask) if not m]

    @classmethod
    def empty(cls, tokens: list) -> "StructureMask":
        return cls(tokens, [False] * len(tokens))

    @classmethod
    def full(cls, tokens: list) -> "StructureMask":
        return cls(tokens, [True] * len(tokens))

    def union(self, other: "StructureMask") -> "StructureMask":
        if len(self.mask) != len(other.mask):
            raise ValueError("Cannot union masks of different lengths")
        return StructureMask(self.tokens, [a or b for a, b in zip(self.mask, other.mask)], {"source": "union", **self.metadata, **other.metadata})

    def intersection(self, other: "StructureMask") -> "StructureMask":
        if len(self.mask) != len(other.mask):
            raise ValueError("Cannot intersect masks of different lengths")
        return StructureMask(self.tokens, [a and b for a, b in zip(self.mask, other.mask)], {"source": "intersection", **self.metadata, **other.metadata})


def mask_to_spans(m: StructureMask) -> list[MaskSpan]:
    if not m.mask:
        return []
    spans, start, cur = [], 0, m.mask[0]
    for i in range(1, len(m.mask)):
        if m.mask[i] != cur:
            spans.append(MaskSpan(start, i, cur))
            start, cur = i, m.mask[i]
    spans.append(MaskSpan(start, len(m.mask), cur))
    return spans


def apply_mask_to_text(text: str, m: StructureMask, compress_fn: Callable[[str], str]) -> str:
    parts = []
    for sp in mask_to_spans(m):
        seg = text[sp.start:sp.end]
        parts.append(seg if sp.is_structural else (compress_fn(seg) if len(seg) > 50 else seg))
    return "".join(parts)


def compute_entropy(text: str) -> float:
    """Normalised (0-1) Shannon entropy of a string."""
    if not text:
        return 0.0
    freq = Counter(text)
    total = len(text)
    ent = -sum((c / total) * math.log2(c / total) for c in freq.values())
    max_ent = math.log2(len(freq)) if len(freq) > 1 else 1
    return ent / max_ent if max_ent > 0 else 0.0


def compute_entropy_mask(tokens: list, threshold: float = 0.85, min_token_length: int = 8) -> StructureMask:
    """Preserve high-entropy tokens (UUIDs, hashes, ...)."""
    mask = [len(t) >= min_token_length and compute_entropy(t) >= threshold for t in tokens]
    return StructureMask(tokens, mask, {"source": "entropy", "threshold": threshold})
