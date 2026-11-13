import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.proxy import cache_plan
from app.router.stream_cache import (META_KEY, StreamAggregator, build_chunks, pieces, replay_delays, split_meta, synthesize_stream)


# ---------------------------------------------------------------- helpers
def parse_sse(lines):
    """['data: {...}\\n\\n', ...] -> list of JSON objects (and '[DONE]')."""
    out = []
    for ln in lines:
        assert ln.startswith("data: ") and ln.endswith("\n\n"), repr(ln)  # strict framing
        body = ln[6:-2]
        out.append("[DONE]" if body == "[DONE]" else json.loads(body))
    return out


def aggregate(lines, public="m"):
    agg = StreamAggregator()
    for o in parse_sse(lines):
        if o != "[DONE]":
            agg.feed(o)
    return agg, agg.result(public)


async def collect(resp, speed="instant", meta=None):
    return [ln async for ln in synthesize_stream(resp, speed, meta)]


def completion(content="Hello world", tool_calls=None, reasoning=None, finish="stop", usage=None, rkey="reasoning_content"):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg[rkey] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "chatcmpl-abc", "object": "chat.completion", "created": 1700000000, "model": "m",
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": usage or {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}


def tc(i, name, args):
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": args}}


# ---------------------------------------------------------------- pieces
@pytest.mark.parametrize("text", ["", "a", "Hello world", "  leading and trailing  ", "line1\nline2\n\nline4", "żółć 😀 emoji — ünïcode text " * 20,
                                  "x" * 500, "word " * 300, '{"a":"' + "y" * 300 + '"}', "\t tabs\tand   spaces "])
def test_pieces_reproduce_text_exactly(text):
    ps = pieces(text)
    assert "".join(ps) == text
    assert all(len(p) <= 64 for p in ps)
    assert all(p for p in ps)


def test_pieces_are_small_for_normal_text():
    ps = pieces("The quick brown fox jumps over the lazy dog. " * 10)
    assert len(ps) > 10 and max(len(p) for p in ps) <= 30


# ---------------------------------------------------------------- round trip: aggregate(synthesize(resp)) == resp
ROUND_TRIPS = {
    "plain text": completion("Hello, how can I help you today? I can write code, explain things, and more."),
    "empty content": completion(""),
    "unicode": completion("Zażółć gęślą jaźń 😀 — ünïcode ✓ " * 12),
    "multiline": completion("line one\n\n  indented line\nlast"),
    "reasoning": completion("42", reasoning="Let me think step by step about the question " * 5),
    "reasoning (alt key)": completion("ok", reasoning="thinking...", rkey="reasoning"),
    "tool call": completion(None, [tc(0, "get_weather", '{"city": "Paris", "unit": "celsius", "days": 5, "verbose": true}')], finish="tool_calls"),
    "two tool calls": completion(None, [tc(0, "a", '{"x": 1}'), tc(1, "b", '{"payload": "' + "z" * 150 + '"}')], finish="tool_calls"),
    "text + tool call": completion("Let me check.", [tc(0, "search", '{"q": "cats and dogs"}')], finish="tool_calls"),
    "length finish": completion("truncated te", finish="length"),
    "long": completion("lorem ipsum dolor sit amet " * 400),
}


@pytest.mark.parametrize("name", list(ROUND_TRIPS))
async def test_round_trip(name):
    resp = ROUND_TRIPS[name]
    lines = await collect(resp)
    agg, back = aggregate(lines)
    assert agg.complete
    assert back == resp, name


async def test_stream_structure_matches_openai_conventions():
    objs = parse_sse(await collect(completion("Hello world", reasoning="hmm", usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})))
    assert objs[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert objs[-1] == "[DONE]"
    assert objs[-2]["choices"] == [] and objs[-2]["usage"]["total_tokens"] == 6              # usage chunk (no choices) before [DONE]
    fin = objs[-3]
    assert fin["choices"][0]["delta"] == {} and fin["choices"][0]["finish_reason"] == "stop"  # finish chunk
    assert all(o["object"] == "chat.completion.chunk" and o["model"] == "m" and o["id"] == "chatcmpl-abc" for o in objs[:-1])
    assert all(o["choices"][0]["finish_reason"] is None for o in objs[:-3])                   # only the last content chunk finishes


async def test_tool_call_deltas_follow_openai_shape():
    objs = parse_sse(await collect(completion(None, [tc(0, "fn", '{"a": 1, "b": 2}')], finish="tool_calls")))
    first = [o for o in objs[1:-3] if o["choices"][0]["delta"].get("tool_calls")][0]["choices"][0]["delta"]["tool_calls"][0]
    assert first == {"index": 0, "id": "call_0", "type": "function", "function": {"name": "fn", "arguments": ""}}
    rest = [o["choices"][0]["delta"]["tool_calls"][0] for o in objs[1:-3] if o["choices"][0]["delta"].get("tool_calls")][1:]
    assert all("id" not in r and r["index"] == 0 for r in rest) and "".join(r["function"]["arguments"] for r in rest) == '{"a": 1, "b": 2}'


# ---------------------------------------------------------------- aggregator on live-style (differently chunked) streams
def feed_all(chunks, **kw):
    agg = StreamAggregator(**kw)
    for c in chunks:
        agg.feed(c)
    return agg


def delta(d, finish=None, idx=0, **extra):
    return {"id": "x1", "created": 5, "model": "upstream-model", "choices": [{"index": idx, "delta": d, "finish_reason": finish, **extra}]}


def test_aggregator_handles_arbitrary_chunking_and_rewrites_model():
    agg = feed_all([delta({"role": "assistant"}), delta({"content": "Hel"}), delta({"content": "lo "}), delta({"content": "world"}),
                    delta({}, "stop"), {"id": "x1", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}}])
    r = agg.result("public")
    assert agg.complete and r["model"] == "public" and r["id"] == "x1" and r["created"] == 5
    assert r["choices"][0]["message"] == {"role": "assistant", "content": "Hello world"} and r["usage"]["total_tokens"] == 12


def test_aggregator_assembles_interleaved_fragmented_tool_calls():
    chunks = [delta({"role": "assistant", "content": None}),
              delta({"tool_calls": [{"index": 0, "id": "c0", "type": "function", "function": {"name": "alpha", "arguments": ""}}]}),
              delta({"tool_calls": [{"index": 1, "id": "c1", "type": "function", "function": {"name": "beta", "arguments": ""}}]}),
              delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"a"'}}]}),
              delta({"tool_calls": [{"index": 1, "function": {"arguments": '{"b": '}}]}),
              delta({"tool_calls": [{"index": 0, "function": {"arguments": ': 1}'}}]}),
              delta({"tool_calls": [{"index": 1, "function": {"arguments": '2}'}}]}), delta({}, "tool_calls")]
    r = feed_all(chunks).result("m")
    calls = r["choices"][0]["message"]["tool_calls"]
    assert r["choices"][0]["message"]["content"] is None and r["choices"][0]["finish_reason"] == "tool_calls"
    assert [(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in calls] == [("c0", "alpha", '{"a": 1}'), ("c1", "beta", '{"b": 2}')]
    assert [json.loads(c["function"]["arguments"]) for c in calls] == [{"a": 1}, {"b": 2}]


def test_aggregator_reasoning_keys():
    r = feed_all([delta({"reasoning_content": "think "}), delta({"reasoning_content": "more"}), delta({"content": "answer"}), delta({}, "stop")]).result("m")
    assert r["choices"][0]["message"]["reasoning_content"] == "think more" and r["choices"][0]["message"]["content"] == "answer"
    r = feed_all([delta({"reasoning": "alt"}), delta({"content": "a"}), delta({}, "stop")]).result("m")
    assert r["choices"][0]["message"]["reasoning"] == "alt" and "reasoning_content" not in r["choices"][0]["message"]


def test_aggregator_refuses_incomplete_and_unsupported_streams():
    assert not feed_all([delta({"content": "partial"})]).complete                          # no finish_reason: stream cut off
    assert not feed_all([]).complete
    assert not feed_all([delta({"content": "a"}), delta({"content": "b"}, idx=1), delta({}, "stop")]).complete   # n > 1
    assert not feed_all([delta({"content": "a"}, logprobs={"content": []}), delta({}, "stop")]).complete          # logprobs
    assert not feed_all([delta({"content": "x" * 600}), delta({}, "stop")], max_bytes=500).complete               # over the size cap
    assert feed_all([delta({"content": "ok"}), delta({}, "stop")]).complete


def test_aggregator_never_raises_on_garbage():
    agg = StreamAggregator()
    for junk in (None, 5, "str", [], {"choices": "nope"}, {"choices": [None]}, {"choices": [{"index": 0, "delta": {"tool_calls": "bad"}}]}):
        agg.feed(junk)
    assert agg.invalid or not agg.complete


def test_aggregator_usage_fallback_and_fingerprint():
    agg = feed_all([{**delta({"content": "hi"}), "system_fingerprint": "fp_1"}, delta({}, "stop")])
    r = agg.result("m", {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
    assert r["usage"] == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4} and r["system_fingerprint"] == "fp_1"


# ---------------------------------------------------------------- pacing
def test_instant_vs_realistic_delays():
    chunks = build_chunks(completion("word " * 200))
    assert set(replay_delays(chunks, {"gen_ms": 2000}, "instant")) == {0.0}
    d = replay_delays(chunks, {"gen_ms": 2000}, "realistic")
    assert len(d) == len(chunks) and d[0] == 0 and d[-1] == 0 and sum(d) == pytest.approx(2.0)   # replays over the original generation time
    assert sum(replay_delays(chunks, {"gen_ms": 60000}, "realistic")) == pytest.approx(10.0)      # capped at 10 s total
    assert sum(replay_delays(chunks, {"gen_ms": 0}, "realistic")) > 0                             # 0 / missing -> estimated from size
    assert sum(replay_delays(chunks, {}, "realistic")) > 0
    assert all(x >= 0 for x in d) and max(d) > 0


async def test_realistic_replay_actually_waits_and_instant_does_not():
    resp = completion("word " * 60)
    t0 = time.time(); await collect(resp, "instant"); instant = time.time() - t0
    t0 = time.time(); await collect(resp, "realistic", {"gen_ms": 400}); real = time.time() - t0
    assert instant < 0.1 and 0.3 < real < 0.8 and real > instant * 3


# ---------------------------------------------------------------- metadata + cache_plan
def test_split_meta_strips_internal_key_without_mutating():
    entry = {**completion("x"), META_KEY: {"gen_ms": 1250, "origin": "stream"}}
    clean, meta = split_meta(entry)
    assert META_KEY not in clean and meta == {"gen_ms": 1250, "origin": "stream"} and META_KEY in entry
    assert split_meta(completion("x"))[1] == {}
    assert clean["choices"][0]["message"]["content"] == "x"  # still a valid non-stream response


def gm(**kw):
    return SimpleNamespace(**{"cache_enabled": True, "cache_any_temperature": False, "cache_streaming": False, "cache_replay_speed": "instant", **kw})


def test_cache_plan_streaming_matrix():
    chat = "chat/completions"
    t0 = {"temperature": 0}
    assert cache_plan(gm(), chat, t0, "", True) == "off"                                           # not opted in
    assert cache_plan(gm(cache_streaming=True), chat, t0, "", True) == "use"
    assert cache_plan(gm(cache_streaming=True), chat, {}, "", True) == "skip"                       # same temperature rule as non-stream
    assert cache_plan(gm(cache_streaming=True), chat, {"temperature": 0.7}, "1", True) == "use"
    assert cache_plan(gm(cache_streaming=True), chat, t0, "0", True) == "bypass"
    assert cache_plan(gm(cache_streaming=True, cache_any_temperature=True), chat, {"temperature": 0.9}, "", True) == "use"
    assert cache_plan(gm(cache_streaming=True), "completions", t0, "", True) == "off"               # legacy completions never
    assert cache_plan(gm(cache_streaming=True), chat, {**t0, "logprobs": True}, "", True) == "off"
    assert cache_plan(gm(cache_streaming=True), chat, {**t0, "top_logprobs": 3}, "", True) == "off"
    assert cache_plan(gm(cache_streaming=True), chat, {**t0, "n": 2}, "", True) == "off"
    assert cache_plan(gm(cache_streaming=True), chat, {**t0, "n": 1}, "", True) == "use"
    assert cache_plan(gm(cache_enabled=False, cache_streaming=True), chat, t0, "1", True) == "off"
    # non-streaming behaviour unchanged
    assert cache_plan(gm(), chat, t0, "", False) == "use" and cache_plan(gm(), chat, {}, "", False) == "skip" and cache_plan(gm(), "embeddings", {}, "", False) == "use"
    assert cache_plan(gm(), chat, {**t0, "n": 2}, "", False) == "use"  # non-stream n>1 is fine (whole JSON stored)
