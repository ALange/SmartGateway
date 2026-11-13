import asyncio

import pytest

from app import discovery
from app.proxy import detect_requirements
from app.router.scheduler import NoCapacity, Scheduler
from app.router.strategies import Candidate, Stats, pick


def cand(i, limit=2, **kw):
    return Candidate(member_id=i, model_id=i, endpoint_id=i, endpoint_name=f"e{i}", base_url="http://x",
                     api_key=None, upstream_id=f"m{i}", caps={"chat"}, limit=limit, **kw)


def test_least_busy_prefers_lower_ratio():
    st = Stats()
    a, b = cand(1, limit=2), cand(2, limit=8)
    st.acquire(a)
    assert pick("least_busy", [a, b], st) is b
    for _ in range(4):
        st.acquire(b)
    st.acquire(b)  # b at 5/8 > a at 1/2
    assert pick("least_busy", [a, b], st) is a


def test_no_pick_when_full():
    st = Stats()
    a = cand(1, limit=1)
    st.acquire(a)
    assert pick("least_busy", [a], st) is None


def test_priority_failover():
    st = Stats()
    a, b = cand(1, priority=0), cand(2, priority=1)
    assert pick("priority_failover", [a, b], st) is a
    st.acquire(a); st.acquire(a)
    assert pick("priority_failover", [a, b], st) is b


def test_weighted_rr_distribution():
    st = Stats()
    a, b = cand(1, limit=100, weight=3), cand(2, limit=100, weight=1)
    got = [pick("weighted_round_robin", [a, b], st).member_id for _ in range(8)]
    assert got.count(1) == 6 and got.count(2) == 2


def test_member_limit():
    st = Stats()
    a = cand(1, limit=10, member_limit=1)
    st.acquire(a)
    assert pick("least_busy", [a], st) is None


async def test_scheduler_queue_fifo_and_timeout():
    s = Scheduler()
    a = cand(1, limit=1)
    first = await s.acquire(1, "least_busy", 1, lambda: [a], 1)
    order = []

    async def waiter(n):
        c = await s.acquire(1, "least_busy", 1, lambda: [a], 2)
        order.append(n)
        await asyncio.sleep(0.01)
        s.release(c)

    t1 = asyncio.create_task(waiter(1)); await asyncio.sleep(0.01)
    t2 = asyncio.create_task(waiter(2)); await asyncio.sleep(0.01)
    assert s.queue_depth() == 2
    s.release(first)
    await asyncio.gather(t1, t2)
    assert order == [1, 2]
    assert s.stats.inflight(1) == 0
    # timeout
    c = await s.acquire(1, "least_busy", 1, lambda: [a], 1)
    with pytest.raises(NoCapacity):
        await s.acquire(1, "least_busy", 1, lambda: [a], 0.1)
    s.release(c)


def test_breaker_opens():
    s = Scheduler()
    for _ in range(3):
        s.breaker.failure(7)
    assert s.breaker.state(7) == "open" and not s.breaker.allow(7)
    s.breaker.success(7)
    assert s.breaker.state(7) == "closed"


def test_requirements_detection():
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x"},
                                                       {"type": "image_url", "image_url": {"url": "u"}}]}],
            "reasoning_effort": "high"}
    hard, soft = detect_requirements("chat/completions", body)
    assert hard == {"chat", "vision"} and soft == {"reasoning"}
    assert detect_requirements("embeddings", {})[0] == {"embedding"}
    assert "ocr" in detect_requirements("chat/completions", {"messages": []}, "ocr")[0]


def test_name_heuristics():
    h = discovery.heuristics_from_name
    assert "embedding" in h("nomic-embed-text") and "embedding" in h("BAAI/bge-m3")
    assert "vision" in h("Qwen2.5-VL-7B") and "reasoning" in h("deepseek-r1-distill")
    assert "audio" in h("whisper-large-v3") and "ocr" in h("deepseek-ocr")
    assert h("llama-3.1-8b-instruct") == set()


def test_metadata_caps():
    c = discovery.caps_from_metadata({"architecture": {"input_modalities": ["text", "image"]},
                                      "supported_parameters": ["reasoning"]})
    assert c == {"vision", "reasoning"}
    assert discovery.caps_from_metadata({"capabilities": ["completion", "vision", "thinking"]}) == {"chat", "vision", "reasoning"}


def test_normalize():
    assert discovery.normalize_base_url("http://h:1/v1/") == "http://h:1/v1"
    assert discovery.normalize_base_url("http://h:1/v1/models") == "http://h:1/v1"
    assert discovery.root_url("http://h:1/v1") == "http://h:1"


def test_extract_context():
    e = discovery.extract_context
    assert e({"meta": {"n_ctx": 262144, "n_ctx_train": 1000000}}) == 262144
    assert e({"id": "x", "max_model_len": 8192}) == 8192
    assert e({"id": "x"}, {"default_generation_settings": {"n_ctx": 4096}}) == 4096
    assert e({"top_provider": {"context_length": 128000}}) == 128000
    assert e({"status": {"args": ["--ctx-size", "32768"]}}) == 32768
    assert e({"id": "x"}) is None
