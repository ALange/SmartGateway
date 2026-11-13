import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.proxy import cache_plan
from app.router.cache import Cache, MemoryBackend, RedisBackend


def fake_backend(prefix="sg:", server=None):
    client = fakeredis.aioredis.FakeRedis(decode_responses=True, server=server)
    return RedisBackend("redis://fake", prefix, client=client), client


async def test_memory_ttl_and_lru():
    m = MemoryBackend(max_entries=2)
    await m.put("a", {"x": 1}, 1)
    await m.put("b", {"x": 2}, 60)
    await m.put("c", {"x": 3}, 60)  # evicts a (oldest)
    assert await m.get("a") is None and (await m.get("b")) == {"x": 2}
    m2 = MemoryBackend()
    await m2.put("k", {"v": 1}, 1)
    assert await m2.get("k") == {"v": 1}
    await asyncio.sleep(1.1)
    assert await m2.get("k") is None


async def test_redis_backend_roundtrip_ttl_prefix_clear():
    rb, client = fake_backend("t1:")
    await rb.put("abc", {"a": 1}, 30)
    assert await rb.get("abc") == {"a": 1}
    ttl = await client.ttl("t1:cache:abc")
    assert 0 < ttl <= 30
    await client.set("other:key", "keep")            # unrelated key
    other, _ = fake_backend("t2:", server=client.connection_pool.connection_kwargs.get("server"))
    assert await rb.count() == 1
    assert await rb.clear() == 1
    assert await rb.get("abc") is None
    assert await client.get("other:key") == "keep"   # clear only touches our prefix


async def test_prefix_isolation_on_shared_server():
    import fakeredis
    server = fakeredis.FakeServer()
    a, _ = fake_backend("A:", server)
    b, _ = fake_backend("B:", server)
    await a.put("k", {"who": "a"}, 30)
    assert await b.get("k") is None and (await a.get("k")) == {"who": "a"}


class Broken:
    """Client whose every command fails, then can be healed."""
    def __init__(self, inner):
        self.inner, self.down = inner, True

    def __getattr__(self, n):
        if self.down:
            async def boom(*a, **k):
                raise ConnectionError("connection refused redis://:secretpw@host:6379/0")
            return boom
        return getattr(self.inner, n)

    def scan_iter(self, *a, **k):
        return self.inner.scan_iter(*a, **k)

    async def aclose(self):
        pass


async def test_failure_falls_back_to_memory_and_recovers():
    inner = fakeredis.aioredis.FakeRedis(decode_responses=True)
    br = Broken(inner)
    c = Cache()
    await c.reconfigure({"redis_enabled": True, "redis_url": "x", "cache_fallback_memory": True, "cache_default_ttl": 60},
                        backend=RedisBackend("redis://x", "sg:", client=br))
    assert c.state == "connected"
    assert await c.put("k", {"v": 1}) is True           # redis fails -> stored in memory
    assert c.state == "error" and "secretpw" not in (c.last_error or "")
    assert await c.get("k") == {"v": 1}                  # still served (within retry window: memory only)
    br.down = False
    c.next_retry = 0                                     # retry window elapsed
    assert await c.get("other") is None                  # redis probed, healthy again
    assert c.state == "connected" and c.last_error is None
    await c.put("k2", {"v": 2})
    assert json.loads(await inner.get("sg:cache:k2")) == {"v": 2}   # now really in redis


async def test_no_fallback_means_no_caching_when_down():
    br = Broken(fakeredis.aioredis.FakeRedis(decode_responses=True))
    c = Cache()
    await c.reconfigure({"redis_enabled": True, "redis_url": "x", "cache_fallback_memory": False},
                        backend=RedisBackend("redis://x", "sg:", client=br))
    assert await c.put("k", {"v": 1}) is False
    assert await c.get("k") is None and c.state == "error"


async def test_oversize_entry_skipped_and_stats():
    c = Cache()
    await c.reconfigure({"redis_enabled": False, "cache_max_entry_kb": 1, "cache_default_ttl": 60})
    assert await c.put("big", {"x": "y" * 5000}) is False
    assert await c.put("small", {"x": "y"}) is True
    assert await c.get("small") and await c.get("nope") is None
    st = await c.stats()
    assert st["backend"] == "memory" and st["hits"] == 1 and st["misses"] == 1 and st["entries"] == 1


def gm(**kw):
    return SimpleNamespace(**{"cache_enabled": True, "cache_any_temperature": False, **kw})


def test_cache_decision_matrix():
    chat = "chat/completions"
    assert cache_plan(gm(), chat, {"temperature": 0}, "", False) == "use"
    assert cache_plan(gm(), chat, {}, "", False) == "skip"                       # no temperature given
    assert cache_plan(gm(), chat, {"temperature": 0.7}, "", False) == "skip"
    assert cache_plan(gm(), chat, {"temperature": 0.7}, "1", False) == "use"      # forced by header
    assert cache_plan(gm(), chat, {"temperature": 0}, "0", False) == "bypass"      # header opt-out wins
    assert cache_plan(gm(cache_any_temperature=True), chat, {"temperature": 0.7}, "", False) == "use"
    assert cache_plan(gm(), chat, {"temperature": 0}, "", True) == "off"          # streaming never cached
    assert cache_plan(SimpleNamespace(cache_enabled=False, cache_any_temperature=True), chat, {"temperature": 0}, "1", False) == "off"
    assert cache_plan(gm(), "embeddings", {}, "", False) == "use"
    assert cache_plan(gm(), "audio/speech", {"temperature": 0}, "", False) == "off"


# ---- settings API + migration
def test_settings_api_split_fields_and_secret_handling(tmp_path):
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    db.init_db()
    c = TestClient(app)
    # explicit fields: URL and username are plain and visible, the password is write-only
    r = c.put("/api/settings", json={"redis_url": "redis://redis.example:6380/2", "redis_username": "cacheuser", "redis_password": "topsecret",
                                     "redis_enabled": True, "redis_key_prefix": "acme:"})
    assert r.status_code == 200, r.text
    body = c.get("/api/settings").json()
    assert body["redis_url"] == "redis://redis.example:6380/2" and body["redis_username"] == "cacheuser" and body["has_password"] is True
    assert "topsecret" not in json.dumps(body) and "redis_password" not in body
    con = sqlite3.connect(str(db.engine.url.database))
    rows = dict(con.execute("select key, value from setting where key like 'redis_%'").fetchall())
    assert json.loads(rows["redis_url"]) == "redis://redis.example:6380/2" and json.loads(rows["redis_username"]) == "cacheuser"
    assert "topsecret" not in rows["redis_password"] and json.loads(rows["redis_password"]).startswith("enc:")
    # keeping fields: omitted values stay, empty string clears
    c.put("/api/settings", json={"redis_username": "other"})
    assert c.get("/api/settings").json()["has_password"] is True and c.get("/api/settings").json()["redis_username"] == "other"
    c.put("/api/settings", json={"redis_password": ""})
    assert c.get("/api/settings").json()["has_password"] is False
    # credentials embedded in the URL are split into their own fields
    c.put("/api/settings", json={"redis_url": "rediss://alice:p%40ss@h.example:6379/1"})
    body = c.get("/api/settings").json()
    assert body["redis_url"] == "rediss://h.example:6379/1" and body["redis_username"] == "alice" and body["has_password"] is True
    from app import settings_store as st
    assert st.get_all()["redis_password"] == "p@ss"  # percent-decoding applied
    # validation
    assert c.put("/api/settings", json={"redis_url": "http://nope"}).status_code == 400
    assert c.put("/api/settings", json={"redis_key_prefix": "bad prefix!"}).status_code == 400
    assert c.put("/api/settings", json={"cache_default_ttl": 0}).status_code == 400
    # unreachable server: clean error, neither a URL password nor the password field leaks
    t = c.post("/api/settings/redis/test", json={"url": "redis://:leakme@127.0.0.1:1/0"}).json()
    assert t["ok"] is False and "leakme" not in json.dumps(t)
    t = c.post("/api/settings/redis/test", json={"url": "redis://127.0.0.1:1/0", "username": "u", "password": "fieldleak"}).json()
    assert t["ok"] is False and "fieldleak" not in json.dumps(t)
    # removing the configuration also removes username and password
    r = c.put("/api/settings", json={"redis_enabled": False, "redis_url": ""}).json()
    assert r["has_url"] is False and r["redis_username"] == "" and r["has_password"] is False
    assert c.put("/api/settings", json={"redis_enabled": True}).status_code == 400   # enabled without a URL


def test_saved_password_only_used_for_saved_server(monkeypatch):
    from fastapi.testclient import TestClient
    from app import db
    from app.api import settings as api_settings
    from app.main import app
    db.init_db()
    c = TestClient(app)
    c.put("/api/settings", json={"redis_url": "redis://saved.example:6379/0", "redis_username": "u1", "redis_password": "savedpw"})
    seen = []

    class FakeBackend:
        def __init__(self, url, prefix="sg:", client=None, username=None, password=None):
            seen.append((url, username, password))

        async def ping(self):
            return 1.0, "7.0"

        async def close(self):
            pass

    monkeypatch.setattr(api_settings, "RedisBackend", FakeBackend)
    c.post("/api/settings/redis/test", json={})                                        # saved config as is
    c.post("/api/settings/redis/test", json={"url": "redis://saved.example:6379/0"})   # same server, password omitted -> saved one
    c.post("/api/settings/redis/test", json={"url": "redis://elsewhere:6379/0"})       # other server -> never the saved password
    c.post("/api/settings/redis/test", json={"url": "redis://elsewhere:6379/0", "password": "typed"})
    assert seen == [("redis://saved.example:6379/0", "u1", "savedpw"), ("redis://saved.example:6379/0", "u1", "savedpw"),
                    ("redis://elsewhere:6379/0", "u1", ""), ("redis://elsewhere:6379/0", "u1", "typed")]
    c.put("/api/settings", json={"redis_url": ""})


def test_split_url_variants():
    from app.settings_store import split_url
    assert split_url("redis://host:6379/0") == ("redis://host:6379/0", "", "")
    assert split_url("redis://:pw@host:6379/0") == ("redis://host:6379/0", "", "pw")
    assert split_url("redis://u:pw@host/3") == ("redis://host/3", "u", "pw")
    assert split_url("rediss://u:p%3Aw@[::1]:6380/1?x=1") == ("rediss://[::1]:6380/1?x=1", "u", "p:w")
    assert split_url("unix:///tmp/redis.sock") == ("unix:///tmp/redis.sock", "", "")
    assert split_url("") == ("", "", "")


def test_env_url_with_credentials_and_legacy_encrypted_url(monkeypatch):
    from app import crypto, db, settings_store as st
    from sqlmodel import Session
    from app.models import Setting
    db.init_db()
    with Session(db.engine) as s:  # clean slate
        for k in ("redis_url", "redis_username", "redis_password", "redis_enabled"):
            row = s.get(Setting, k)
            if row:
                s.delete(row)
        s.commit()
    monkeypatch.setenv("SG_REDIS_URL", "redis://envuser:envpw@env.example:6379/4")
    eff = st.get_all()
    assert (eff["redis_url"], eff["redis_username"], eff["redis_password"], eff["redis_enabled"]) == ("redis://env.example:6379/4", "envuser", "envpw", True)
    # a URL saved (encrypted, with credentials) by the previous version is read and split transparently
    with Session(db.engine) as s:
        s.add(Setting(key="redis_url", value=json.dumps(crypto.encrypt("redis://:oldpw@legacy.example:6379/0"))))
        s.commit()
    eff = st.get_all()
    assert (eff["redis_url"], eff["redis_username"], eff["redis_password"]) == ("redis://legacy.example:6379/0", "", "oldpw")
    with Session(db.engine) as s:
        s.delete(s.get(Setting, "redis_url"))
        s.commit()
    st._cached["v"] = None


async def test_backend_passes_credentials_to_client():
    rb = RedisBackend("redis://h.example:6379/0", "sg:", username="alice", password="pw")
    kw = rb.r.connection_pool.connection_kwargs
    assert kw["username"] == "alice" and kw["password"] == "pw" and kw["host"] == "h.example"
    await rb.close()
    rb2 = RedisBackend("redis://h.example:6379/0", "sg:")
    assert rb2.r.connection_pool.connection_kwargs.get("password") is None
    await rb2.close()


def test_gateway_cache_mode_migrates_to_models(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from app import db
    path = tmp_path / "v2.db"
    c = sqlite3.connect(path)
    c.executescript("""
    create table gateway (id integer primary key, slug text, name text, api_key text, strategy text, cache_mode text, queue_timeout real, max_retries integer, created_at real);
    create table gatewaymodel (id integer primary key, gateway_id integer, public_name text, strategy text, sticky integer, system_prompt text, max_tokens_cap integer, default_params text);
    insert into gateway values (1,'on','On',null,'least_busy','exact',60,2,0);
    insert into gateway values (2,'off','Off',null,'least_busy','off',60,2,0);
    insert into gatewaymodel values (1,1,'m1',null,0,null,null,null);
    insert into gatewaymodel values (2,1,'m2',null,0,null,null,null);
    insert into gatewaymodel values (3,2,'m3',null,0,null,null,null);
    """)
    c.commit(); c.close()
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db, "engine", eng)
    db.init_db()
    c = sqlite3.connect(path)
    assert dict(c.execute("select public_name, cache_enabled from gatewaymodel")) == {"m1": 1, "m2": 1, "m3": 0}
    assert dict(c.execute("select slug, cache_mode from gateway")) == {"on": "off", "off": "off"}
    c.execute("update gatewaymodel set cache_enabled = 0 where public_name='m1'"); c.commit()
    db.init_db()  # idempotent: must not re-enable what the user switched off
    assert dict(c.execute("select public_name, cache_enabled from gatewaymodel"))["m1"] == 0
