"""Per-API-key rate limits (RPM/TPM/concurrency) and token quotas (daily/monthly)."""
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlmodel import select

from ..db import session
from ..models import RequestLog


class LimitError(Exception):
    def __init__(self, message: str, retry_after: int = 5, status: int = 429, code: str = "rate_limit_exceeded"):
        super().__init__(message)
        self.message, self.retry_after, self.status, self.code = message, retry_after, status, code


@dataclass
class Token:
    limiter: "Limiter"
    key_id: Optional[int]
    entry: Optional[list] = None
    tokens: int = 0
    released: bool = False

    def release(self, tokens: Optional[int] = None) -> None:
        if self.released:
            return
        self.released = True
        self.limiter._finish(self, tokens)


def _period_starts() -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day.timestamp(), month.timestamp()


class Limiter:
    def __init__(self):
        self.windows: dict[int, deque] = {}
        self.inflight: dict[int, int] = {}
        self.usage_cache: dict[int, list] = {}  # key_id -> [loaded_at, day_tokens, month_tokens]

    def _usage(self, key_id: int) -> tuple[int, int]:
        c = self.usage_cache.get(key_id)
        day0, month0 = _period_starts()
        if not c or time.time() - c[0] > 30 or c[0] < day0:
            with session() as s:
                def total(since):
                    r = s.exec(select(func.coalesce(func.sum(RequestLog.prompt_tokens + RequestLog.completion_tokens), 0))
                               .where(RequestLog.key_id == key_id, RequestLog.ts >= since)).one()
                    return int(r or 0)
                c = [time.time(), total(day0), total(month0)]
            self.usage_cache[key_id] = c
        return c[1], c[2]

    def usage(self, key_id: int) -> dict:
        d, m = self._usage(key_id)
        w = self.windows.get(key_id, deque())
        now = time.time()
        recent = [e for e in w if now - e[0] < 60]
        return {"day_tokens": d, "month_tokens": m, "rpm_now": len(recent), "tpm_now": sum(e[1] for e in recent),
                "in_flight": self.inflight.get(key_id, 0)}

    def admit(self, key, est_tokens: int = 0) -> Token:
        """Check limits for an ApiKey (or None = no key) and register the request."""
        if key is None:
            return Token(self, None)
        now = time.time()
        w = self.windows.setdefault(key.id, deque())
        while w and now - w[0][0] >= 60:
            w.popleft()
        if key.rpm_limit and len(w) >= key.rpm_limit:
            raise LimitError(f"Rate limit reached for this API key ({key.rpm_limit} requests/min)",
                             max(1, int(60 - (now - w[0][0])) + 1))
        if key.tpm_limit and sum(e[1] for e in w) + est_tokens > key.tpm_limit:
            raise LimitError(f"Token rate limit reached for this API key ({key.tpm_limit} tokens/min)",
                             max(1, int(60 - (now - w[0][0])) + 1))
        if key.max_concurrent and self.inflight.get(key.id, 0) >= key.max_concurrent:
            raise LimitError(f"Too many concurrent requests for this API key (max {key.max_concurrent})", 2)
        if key.daily_token_quota or key.monthly_token_quota:
            d, m = self._usage(key.id)
            if key.daily_token_quota and d >= key.daily_token_quota:
                raise LimitError("Daily token quota exhausted for this API key", 3600, 429, "insufficient_quota")
            if key.monthly_token_quota and m >= key.monthly_token_quota:
                raise LimitError("Monthly token quota exhausted for this API key", 86400, 429, "insufficient_quota")
        entry = [now, est_tokens]
        w.append(entry)
        self.inflight[key.id] = self.inflight.get(key.id, 0) + 1
        return Token(self, key.id, entry)

    def _finish(self, tok: Token, tokens: Optional[int]) -> None:
        if tok.key_id is None:
            return
        self.inflight[tok.key_id] = max(0, self.inflight.get(tok.key_id, 0) - 1)
        if tokens is not None and tok.entry is not None:
            tok.entry[1] = tokens
            c = self.usage_cache.get(tok.key_id)
            if c:
                c[1] += tokens
                c[2] += tokens


limiter = Limiter()
