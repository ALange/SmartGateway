"""Search/grep result compressor (TinyClaude/Headroom pattern): keeps paths and line numbers, caps files, matches and snippets."""
import re
from dataclasses import dataclass

GREP_LINE = re.compile(r"^(\S+?):(\d+)(?::(\d+))?:(.*)$")
COMMON_EXT = re.compile(r"\.(?:ts|js|tsx|jsx|mjs|cjs|mts|cts|py|rb|go|rs|java|kt|swift|c|cpp|h|hpp|cs|json|yaml|yml|toml|xml|html|css|scss|md|txt|log|sh|bash|sql|proto|gradle|dart|lua|php|r|scala|zig|nim|ex|exs)$", re.I)
FILE_HEADER = re.compile(r"^──\s(.+)\s──$")
MAX_LINE_NUMBER = 100_000


def _plausible(m: re.Match) -> bool:
    n = int(m.group(2))
    if n < 1 or n > MAX_LINE_NUMBER:
        return False
    path = m.group(1)
    return "/" in path or bool(COMMON_EXT.search(path))


@dataclass
class SearchCompressorConfig:
    max_results: int = 30
    max_lines_per_file: int = 10
    max_snippet_chars: int = 200


@dataclass
class SearchCompressionResult:
    compressed: str
    original_files: int
    compressed_files: int
    original_matches: int
    compressed_matches: int
    strategy: str


class SearchCompressor:
    def __init__(self, config: SearchCompressorConfig | None = None):
        self.c = config or SearchCompressorConfig()

    def compress(self, content: str) -> SearchCompressionResult:
        lines = content.split("\n")
        files: dict[str, list[str]] = {}
        current = "__stdin__"
        total_matches = 0
        for ln in lines:
            h = FILE_HEADER.match(ln)
            if h:
                current = h.group(1)
                continue
            m = GREP_LINE.match(ln)
            if m and _plausible(m):
                current = m.group(1)
                total_matches += 1
            files.setdefault(current, []).append(ln)

        for f, fl in list(files.items()):
            if len(fl) > self.c.max_lines_per_file:
                half = self.c.max_lines_per_file // 2
                files[f] = [*fl[:half], f"...[{len(fl) - self.c.max_lines_per_file} more matches]...", *fl[-(self.c.max_lines_per_file - half):]]

        entries = list(files.items())
        if len(entries) <= self.c.max_results:
            kept = entries
        else:
            half = self.c.max_results // 2
            kept = [*entries[:half], (f"...[{len(entries) - self.c.max_results} more files]...", []), *entries[-(self.c.max_results - half):]]

        out, truncated = [], 0
        for f, fl in kept:
            if not fl:
                out.append(f)
                continue
            for ln in fl:
                m = GREP_LINE.match(ln)
                if m and _plausible(m) and len(m.group(4)) > self.c.max_snippet_chars:
                    truncated += 1
                    out.append(f"{m.group(1)}:{m.group(2)}:{m.group(4)[:self.c.max_snippet_chars]}...[truncated]")
                else:
                    out.append(ln)
        return SearchCompressionResult("\n".join(out), len(files), len(kept), total_matches, total_matches - truncated, "search_preserve_paths")
