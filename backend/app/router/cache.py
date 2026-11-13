"""Response cache: in-memory LRU by default, optional Redis backend (configured in Settings).

A cache problem never fails a request. If Redis errors, we fall back to memory (when enabled) and retry later.
"""
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any, Optional

RETRY_AFTER = float(os.environ.get("SG_CACHE_RETRY_AFTER", "15"))  # seconds between reconnect attempts while Redis is down


def make_key(gateway_id: int, route: str, body: dict) -> str:
    b = {k: v for k, v in body.items() if k not in ("stream", "stream_options", "user")}
    raw = json.dumps([gateway_id, route, b], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class MemoryBackend:
    name = "memory"

    def __init__(self, max_entries: int = 500):
        self.max = max_entries
        self._d: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()

    async def get(self, key: str):
        v = self._d.get(key)
        if not v:
            return None
        if v[0] < time.time():
            self._d.pop(key, None)
            return None
        self._d.move_to_end(key)
        return v[1]

    async def put(self, key: str, value, ttl: int) -> None:
        self._d[key] = (time.time() + ttl, value)
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            self._d.popitem(last=False)

    async def clear(self) -> int:
        n = len(self._d)
        self._d.clear()
        return n

    async def count(self) -> int:
        now = time.time()
        return sum(1 for v in self._d.values() if v[0] >= now)

    async def close(self) -> None:
        pass


class RedisBackend:
    name = "redis"

    def __init__(self, url: str, prefix: str = "sg:", client=None, username: Optional[str] = None, password: Optional[str] = None):
        self.prefix = prefix
        if client is None:
            import redis.asyncio as redis

            client = redis.from_url(url, username=username or None, password=password or None, socket_timeout=0.5,
                                    socket_connect_timeout=0.5, decode_responses=True, health_check_interval=30)
        self.r = client

    def _k(self, key: str) -> str:
        return f"{self.prefix}cache:{key}"

    async def get(self, key: str):
        raw = await self.r.get(self._k(key))
        return json.loads(raw) if raw else None

    async def put(self, key: str, value, ttl: int) -> None:
        await self.r.set(self._k(key), json.dumps(value, separators=(",", ":")), ex=max(1, int(ttl)))

    async def clear(self) -> int:
        n, batch = 0, []
        async for k in self.r.scan_iter(match=f"{self.prefix}cache:*", count=500):
            batch.append(k)
            if len(batch) >= 500:
                n += await self.r.unlink(*batch)
                batch = []
        if batch:
            n += await self.r.unlink(*batch)
        return n

    async def count(self, cap: int = 10000) -> int:
        n = 0
        async for _ in self.r.scan_iter(match=f"{self.prefix}cache:*", count=500):
            n += 1
            if n >= cap:
                break
        return n

    async def ping(self) -> tuple[float, str]:
        t0 = time.time()
        await self.r.ping()
        lat = (time.time() - t0) * 1000
        info = await self.r.info("server")
        return lat, str(info.get("redis_version", "?"))

    async def close(self) -> None:
        try:
            await self.r.aclose()
        except Exception:  # noqa: BLE001
            pass


class Cache:
    def __init__(self):
        self.memory = MemoryBackend()
        self.redis: Optional[RedisBackend] = None
        self.settings: dict = {"cache_default_ttl": 600, "cache_max_entry_kb": 512, "cache_fallback_memory": True}
        self.state = "memory"  # memory | connected | error
        self.last_error: Optional[str] = None
        self.next_retry = 0.0
        self.hits = self.misses = self.errors = self.stores = 0

    # -- configuration ---------------------------------------------------
    async def reconfigure(self, s: dict, backend: Optional[RedisBackend] = None) -> None:
        """Apply settings (called at startup and whenever the Settings page saves)."""
        old = self.redis
        self.settings = {k: s[k] for k in ("cache_default_ttl", "cache_max_entry_kb", "cache_fallback_memory") if k in s} or self.settings
        self.redis, self.last_error, self.next_retry = None, None, 0.0
        if s.get("redis_enabled") and (backend or s.get("redis_url")):
            try:
                _SECRETS[:] = [s["redis_password"]] if s.get("redis_password") else []
                self.redis = backend or RedisBackend(s["redis_url"], s.get("redis_key_prefix") or "sg:",
                                                     username=s.get("redis_username"), password=s.get("redis_password"))
                self.state = "connected"  # optimistic; the first operation confirms
            except Exception as e:  # noqa: BLE001
                self.state, self.last_error = "error", _safe(e)
        else:
            self.state = "memory"
        if old is not None:
            await old.close()

    @property
    def default_ttl(self) -> int:
        return int(self.settings.get("cache_default_ttl") or 600)

    # -- operations ------------------------------------------------------
    def _use_redis(self) -> bool:
        if self.redis is None:
            return False
        return self.state != "error" or time.time() >= self.next_retry

    def _fail(self, e: Exception) -> None:
        self.errors += 1
        self.state, self.last_error = "error", _safe(e)
        self.next_retry = time.time() + RETRY_AFTER

    async def get(self, key: str):
        val = None
        if self._use_redis():
            try:
                val = await self.redis.get(key)
                if self.state == "error":
                    self.state, self.last_error = "connected", None
            except Exception as e:  # noqa: BLE001
                self._fail(e)
                if self.settings.get("cache_fallback_memory", True):
                    val = await self.memory.get(key)
        elif self.redis is None or self.settings.get("cache_fallback_memory", True):
            val = await self.memory.get(key)
        self.hits += val is not None
        self.misses += val is None
        return val

    async def put(self, key: str, value, ttl: Optional[int] = None) -> bool:
        ttl = int(ttl or self.default_ttl)
        try:
            size = len(json.dumps(value, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            return False
        if size > int(self.settings.get("cache_max_entry_kb", 512)) * 1024:
            return False
        self.stores += 1
        if self._use_redis():
            try:
                await self.redis.put(key, value, ttl)
                if self.state == "error":
                    self.state, self.last_error = "connected", None
                return True
            except Exception as e:  # noqa: BLE001
                self._fail(e)
                if not self.settings.get("cache_fallback_memory", True):
                    return False
        elif self.redis is not None and not self.settings.get("cache_fallback_memory", True):
            return False
        await self.memory.put(key, value, ttl)
        return True

    async def clear(self) -> dict:
        out = {"memory": await self.memory.clear(), "redis": 0}
        if self.redis is not None:
            try:
                out["redis"] = await self.redis.clear()
            except Exception as e:  # noqa: BLE001
                self._fail(e)
        return out

    async def stats(self) -> dict:
        entries: Optional[int] = None
        try:
            if self.redis is not None and self._use_redis():
                entries = await self.redis.count()
                if self.state == "error":
                    self.state, self.last_error = "connected", None
            else:
                entries = await self.memory.count()
        except Exception as e:  # noqa: BLE001
            self._fail(e)
            entries = await self.memory.count()
        total = self.hits + self.misses
        return {"backend": "redis" if self.redis is not None else "memory", "state": self.state, "last_error": self.last_error,
                "hits": self.hits, "misses": self.misses, "errors": self.errors, "stores": self.stores,
                "hit_rate": (self.hits / total) if total else None, "entries": entries,
                "memory_entries": await self.memory.count(), "default_ttl": self.default_ttl}


_SECRETS: list[str] = []  # passwords currently in use; scrubbed from any error text that reaches the UI or logs


def _safe(e: Exception, extra: tuple = ()) -> str:
    from ..settings_store import redact_url

    msg = redact_url(f"{type(e).__name__}: {e}")
    for secret in [*_SECRETS, *extra]:
        if secret:
            msg = msg.replace(secret, "***")
    return msg[:200]


cache = Cache()
