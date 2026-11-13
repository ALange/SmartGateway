"""Slot-aware scheduler with a fair FIFO wait queue and a circuit breaker."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .strategies import Candidate, Stats, pick


class NoCapacity(Exception):
    pass


class NoCandidates(Exception):
    pass


@dataclass
class Waiter:
    gateway_id: int
    strategy: str
    key: object
    get_candidates: Callable[[], list]
    future: asyncio.Future
    since: float = field(default_factory=time.time)


class Breaker:
    """Per-model circuit breaker."""

    def __init__(self, threshold: int = 3, cooldown: float = 20.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures: dict[int, int] = {}
        self.opened_at: dict[int, float] = {}
        self.probing: set[int] = set()

    def allow(self, model_id: int) -> bool:
        t = self.opened_at.get(model_id)
        if t is None:
            return True
        if time.time() - t >= self.cooldown and model_id not in self.probing:
            self.probing.add(model_id)  # half-open: let one request through
            return True
        return False

    def state(self, model_id: int) -> str:
        t = self.opened_at.get(model_id)
        if t is None:
            return "closed"
        return "half_open" if time.time() - t >= self.cooldown else "open"

    def success(self, model_id: int) -> None:
        self.failures.pop(model_id, None)
        self.opened_at.pop(model_id, None)
        self.probing.discard(model_id)

    def failure(self, model_id: int) -> None:
        self.probing.discard(model_id)
        n = self.failures.get(model_id, 0) + 1
        self.failures[model_id] = n
        if n >= self.threshold:
            self.opened_at[model_id] = time.time()


class Scheduler:
    def __init__(self):
        self.stats = Stats()
        self.breaker = Breaker()
        self.waiters: list[Waiter] = []

    def queue_depth(self, gateway_id: Optional[int] = None) -> int:
        return sum(1 for w in self.waiters if gateway_id is None or w.gateway_id == gateway_id)

    def _usable(self, cands: list[Candidate]) -> list[Candidate]:
        return [c for c in cands if self.breaker.state(c.model_id) != "open"]

    async def acquire(
        self,
        gateway_id: int,
        strategy: str,
        key: object,
        get_candidates: Callable[[], list],
        timeout: float,
        on_queued: Optional[Callable[[], None]] = None,
    ) -> Candidate:
        """Wait (FIFO) until a candidate with a free slot is available and reserve it."""
        cands = get_candidates()
        if not cands:
            raise NoCandidates()
        mine = {c.model_id for c in cands}
        ahead = any(mine & {c.model_id for c in w.get_candidates()} for w in self.waiters)
        if not ahead:  # fast path: nobody queued for the same upstream models
            c = pick(strategy, self._usable(cands), self.stats, key)
            if c:
                self.stats.acquire(c)
                return c
        fut = asyncio.get_running_loop().create_future()
        w = Waiter(gateway_id, strategy, key, get_candidates, fut)
        self.waiters.append(w)
        if on_queued:
            on_queued()
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise NoCapacity()
        except asyncio.CancelledError:
            # client went away; if a slot was already handed to us, give it back
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                self.stats.release(fut.result())
                self.dispatch()
            raise
        finally:
            if w in self.waiters:
                self.waiters.remove(w)

    def release(self, c: Candidate, ok: bool = True, latency: Optional[float] = None) -> None:
        self.stats.release(c)
        if ok:
            self.breaker.success(c.model_id)
            if latency is not None:
                self.stats.record_latency(c.model_id, latency)
        self.dispatch()

    def fail(self, c: Candidate) -> None:
        self.breaker.failure(c.model_id)

    async def run_ticker(self, interval: float = 0.5) -> None:
        """Re-check waiters periodically (fallback grace periods, health changes, external slot changes)."""
        while True:
            await asyncio.sleep(interval)
            if self.waiters:
                self.dispatch()

    def dispatch(self) -> None:
        """Hand freed slots to waiting requests in FIFO order."""
        for w in list(self.waiters):
            if w.future.done():
                continue
            cands = w.get_candidates()
            if not cands:
                w.future.set_exception(NoCandidates())
                continue
            c = pick(w.strategy, self._usable(cands), self.stats, w.key)
            if c:
                self.stats.acquire(c)
                w.future.set_result(c)
                self.waiters.remove(w)


scheduler = Scheduler()
