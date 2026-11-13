"""Log compressor (TinyClaude/Headroom pattern).

Keeps error/warn lines, collapses runs of identical lines into `[Nx] line`, and caps the total line count.
Deviations from TinyClaude: original line order is preserved (it moved priority lines to the top), and timestamped lines
are *not* automatically pinned (that made typical logs incompressible) unless `pin_timestamps` is set.
"""
import re
from dataclasses import dataclass, field

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


@dataclass
class LogCompressorConfig:
    max_lines: int = 100
    preserve_levels: list = field(default_factory=lambda: ["ERROR", "FATAL", "WARN", "error", "Error"])
    deduplicate: bool = True
    pin_timestamps: bool = False


@dataclass
class LogCompressionResult:
    compressed: str
    original_lines: int
    compressed_lines: int
    strategy: str


class LogCompressor:
    def __init__(self, config: LogCompressorConfig | None = None):
        self.c = config or LogCompressorConfig()

    def _priority(self, line: str) -> bool:
        return any(lv in line for lv in self.c.preserve_levels) or (self.c.pin_timestamps and bool(TIMESTAMP.search(line)))

    def compress(self, content: str) -> LogCompressionResult:
        lines = content.split("\n")
        # 1) collapse runs of identical normal lines
        items: list[tuple[str, bool]] = []  # (text, is_priority)
        i = 0
        while i < len(lines):
            ln = lines[i]
            prio = self._priority(ln)
            if self.c.deduplicate and not prio:
                j = i
                while j + 1 < len(lines) and lines[j + 1] == ln:
                    j += 1
                n = j - i + 1
                items.append((f"[{n}x] {ln}" if n > 1 else ln, False))
                i = j + 1
            else:
                items.append((ln, prio))
                i += 1
        # 2) budget: keep all priority lines, spend the rest on the first/last normal lines
        if len(items) > self.c.max_lines:
            n_prio = sum(1 for _, p in items if p)
            budget = max(10, self.c.max_lines - n_prio)
            normals = [k for k, (_, p) in enumerate(items) if not p]
            if len(normals) > budget:
                half = budget // 2
                keep = set(normals[:half]) | set(normals[len(normals) - (budget - half):])
                dropped = len(normals) - budget
                out, marked = [], False
                for k, (txt, p) in enumerate(items):
                    if p or k in keep:
                        out.append(txt)
                    elif not marked:  # first dropped line: leave one marker in its place
                        out.append(f"...[{dropped} log lines compressed]...")
                        marked = True
                items = [(t, False) for t in out]
        res = [t for t, _ in items]
        return LogCompressionResult("\n".join(res), len(lines), len(res), "log_preserve_levels")
