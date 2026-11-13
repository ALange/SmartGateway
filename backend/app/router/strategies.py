"""Candidate selection strategies. Pure functions over Candidate objects."""
import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    member_id: int
    model_id: int
    endpoint_id: int
    endpoint_name: str
    base_url: str
    api_key: Optional[str]
    upstream_id: str
    caps: set
    limit: int  # effective slot limit on the upstream model
    member_limit: Optional[int] = None
    weight: int = 1
    priority: int = 0
    healthy: bool = True
    context_length: Optional[int] = None
    is_fallback: bool = False
    tier: int = 2
    provider: Optional[str] = None  # hosted catalog this endpoint belongs to (opencode-zen / opencode-go)


class Stats:
    """Runtime counters shared across the process."""

    def __init__(self):
        self.model_inflight: dict[int, int] = {}
        self.external_busy: dict[int, int] = {}  # slots busy with traffic that did not come through us
        self.member_inflight: dict[int, int] = {}
        self.ewma_latency: dict[int, float] = {}  # model_id -> seconds (ttft/total)
        self.rr_counter: dict[int, int] = {}  # gateway_model_id -> counter
        self.rr_credit: dict[int, float] = {}  # member_id -> smooth WRR credit

    def inflight(self, model_id: int) -> int:
        return self.model_inflight.get(model_id, 0)

    def busy(self, model_id: int) -> int:
        return self.inflight(model_id) + self.external_busy.get(model_id, 0)

    def has_free_slot(self, c: Candidate) -> bool:
        if self.busy(c.model_id) >= max(1, c.limit):
            return False
        if c.member_limit is not None and self.member_inflight.get(c.member_id, 0) >= max(1, c.member_limit):
            return False
        return True

    def load(self, c: Candidate) -> float:
        return self.busy(c.model_id) / max(1, c.limit)

    def acquire(self, c: Candidate) -> None:
        self.model_inflight[c.model_id] = self.inflight(c.model_id) + 1
        self.member_inflight[c.member_id] = self.member_inflight.get(c.member_id, 0) + 1

    def release(self, c: Candidate) -> None:
        self.model_inflight[c.model_id] = max(0, self.inflight(c.model_id) - 1)
        self.member_inflight[c.member_id] = max(0, self.member_inflight.get(c.member_id, 0) - 1)

    def record_latency(self, model_id: int, seconds: float) -> None:
        old = self.ewma_latency.get(model_id)
        self.ewma_latency[model_id] = seconds if old is None else old * 0.7 + seconds * 0.3


def pick(strategy: str, cands: list[Candidate], stats: Stats, key=0) -> Optional[Candidate]:
    """Choose among candidates that currently have a free slot.

    `key` may be a hint dict: {"prefer_member": id} (sticky routing), {"tier": n} (tiered strategy).
    """
    hint = key if isinstance(key, dict) else {}
    free = [c for c in cands if stats.has_free_slot(c)]
    if not free:
        return None
    pref = hint.get("prefer_member")
    if pref is not None:
        for c in free:
            if c.member_id == pref:
                return c
    if strategy == "tiered":
        need = hint.get("tier", 1)
        top = max((c.tier for c in cands), default=1)
        eligible = [c for c in free if c.tier >= min(need, top)]
        if not eligible:
            return None  # wait for a big-enough model rather than downgrading
        lowest = min(c.tier for c in eligible)
        group = [c for c in eligible if c.tier == lowest]
        return min(group, key=lambda c: (stats.load(c), c.priority, stats.ewma_latency.get(c.model_id, 0.0)))
    if strategy == "priority_failover":
        best = min(c.priority for c in free)
        group = [c for c in free if c.priority == best]
        return min(group, key=lambda c: (stats.load(c), c.member_id))
    if strategy == "weighted_round_robin":
        # smooth weighted round robin (nginx style)
        total = sum(max(1, c.weight) for c in free)
        for c in free:
            stats.rr_credit[c.member_id] = stats.rr_credit.get(c.member_id, 0.0) + max(1, c.weight)
        chosen = max(free, key=lambda c: stats.rr_credit[c.member_id])
        stats.rr_credit[chosen.member_id] -= total
        return chosen
    if strategy == "fastest":
        return min(free, key=lambda c: (stats.ewma_latency.get(c.model_id, 0.0), stats.load(c)))
    if strategy == "random":
        return random.choice(free)
    # least_busy (default): lowest load ratio, then by latency, then priority
    return min(
        free,
        key=lambda c: (stats.load(c), c.priority, stats.ewma_latency.get(c.model_id, 0.0) / max(1, c.weight)),
    )
