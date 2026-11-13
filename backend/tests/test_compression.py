"""Ports of TinyClaude's compression tests (content-router, json-compressor, cache, compression-step) + new coverage."""
import copy
import json
import re

import pytest

from app.compression import pipeline
from app.compression.aligner import (analyze_alignment, classify_token, compute_alignment_score, detect_volatile_content)
from app.compression.cache import (CompressionCache, content_hash, extract_tool_result_contents, is_tool_result_message,
                                   swap_tool_result_content_at)
from app.compression.compressors.json_c import JSONCompressor
from app.compression.compressors.log_c import LogCompressor, LogCompressorConfig
from app.compression.compressors.search_c import SearchCompressor
from app.compression.compressors.simple_c import SimpleCompressor, SimpleCompressorConfig
from app.compression.context import analyze_context
from app.compression.detector import ContentType, detect_content_type
from app.compression.insights import aggregate_compression_rows, build_compression_response, build_context_response
from app.compression.mask import StructureMask, apply_mask_to_text, compute_entropy, compute_entropy_mask, mask_to_spans
from app.compression.router import ContentRouter, ContentRouterConfig


# ============================================================ ContentRouter (ported content-router.test.ts)
class TestRouterShort:
    router = ContentRouter(ContentRouterConfig(min_content_chars=100))

    def test_minifies_short_pretty_json(self):
        original = json.dumps({"a": 1, "b": 2}, indent=2)
        assert len(original) < 100
        r = self.router.compress(original)
        assert len(r.compressed) < len(original)
        assert r.compressor_used == "JSONMinifier" and r.strategy == "json_minify_short"
        assert json.loads(r.compressed) == {"a": 1, "b": 2}
        assert r.content_type == ContentType.JSON and r.detection_confidence == 1.0

    def test_already_minified_json_not_spuriously_compressed(self):
        original = json.dumps({"a": 1, "b": 2}, separators=(",", ":"))
        r = self.router.compress(original)
        assert r.strategy == "passthrough(too_short)" and r.compressor_used == "passthrough" and r.compressed == original

    @pytest.mark.parametrize("text", ["hello there, short message", "42", "true"])
    def test_short_non_container_content_untouched(self, text):
        r = self.router.compress(text)
        assert r.strategy == "passthrough(too_short)" and r.compressed == text


def test_router_long_json_uses_real_compressor():
    obj = {"key1": "this is a reasonably long string value", "key2": "another long value to make the object bigger", "key3": 123, "key4": True}
    r = ContentRouter().compress(json.dumps(obj, indent=2))
    assert r.compressor_used in ("JSONCompressor", "SimpleCompressor", "passthrough")
    assert r.strategy != "json_minify_short"


def test_router_routes_by_type():
    big_list = json.dumps([{"id": i, "name": f"n{i}", "desc": "x" * 40} for i in range(100)])
    assert ContentRouter().compress(big_list).compressor_used == "JSONCompressor"
    log = "\n".join(f"2024-05-01 10:00:{i % 60:02d} INFO worker tick" for i in range(300))
    assert ContentRouter().compress(log).compressor_used == "LogCompressor"
    grep = "\n".join(f"src/mod{f}.py:{n}:    result = compute_value({n})  # match" for f in range(10) for n in range(1, 41))
    r = ContentRouter().compress(grep)
    assert r.compressor_used == "SearchCompressor" and len(r.compressed) < len(grep)
    prose = " ".join(["The quick brown fox jumps over the lazy dog."] * 80)
    assert ContentRouter().compress(prose).compressor_used == "SimpleCompressor"


def test_router_mixed_content_split_and_disabled_compressors():
    mixed = ("Intro text\n```json\n" + json.dumps([{"id": i, "v": "y" * 60} for i in range(40)]) + "\n```\n\n"
             + "\n".join(f"lib/x{i}.py:{i}:" + "z" * 300 for i in range(40)))
    r = ContentRouter().compress(mixed)
    assert r.was_mixed and r.compressor_used == "ContentRouter(mixed)" and len(r.sub_results) >= 2
    off = ContentRouter(ContentRouterConfig(enable_json=False, enable_log=False, enable_search=False, enable_simple=False))
    r2 = off.compress("word " * 200)
    assert r2.compressed == "word " * 200 and r2.strategy == "passthrough(no_compressor_enabled)"


def test_router_config_from_dict_partial_and_unknown_keys():
    cfg = ContentRouterConfig.from_dict({"min_content_chars": 10, "json": {"max_items": 3, "bogus": 1}, "nonsense": 5, "enable_log": False})
    assert cfg.min_content_chars == 10 and cfg.json.max_items == 3 and cfg.enable_log is False and cfg.simple.compression_ratio == 0.3
    assert ContentRouterConfig.from_dict(cfg.to_dict()).json.max_items == 3  # round-trip


# ============================================================ Detector
def test_detector_matrix():
    d = detect_content_type
    assert d("").content_type == ContentType.UNKNOWN
    assert d('{"a": 1}').content_type == ContentType.JSON
    assert d("{not json}").content_type != ContentType.JSON
    assert d("diff --git a/x b/x\n--- a/x\n+++ b/x").content_type == ContentType.DIFF
    assert d("src/app.py:12:def main():").label == "search_result"
    assert d("ERROR a\nWARN b\nINFO c\nDEBUG d").content_type == ContentType.LOG
    c = d("def add(a, b):\n    return a + b")
    assert c.content_type == ContentType.CODE and c.language == "python"
    assert d("# Title\nsome text").content_type == ContentType.MARKDOWN
    assert d("just some plain words here").content_type == ContentType.TEXT


# ============================================================ JSONCompressor (ported json-compressor.test.ts)
class TestJSON:
    def test_object_key_counts(self):
        r = JSONCompressor().compress(json.dumps({"a": 1, "b": 2, "c": 3, "d": 4}))
        assert (r.original_items, r.compressed_items, r.strategy) == (4, 4, "object_preserve_keys")
        r = JSONCompressor().compress("{}")
        assert (r.original_items, r.compressed_items) == (0, 0)
        r = JSONCompressor().compress("42")
        assert (r.original_items, r.compressed_items, r.strategy) == (1, 1, "passthrough")

    def test_embedded_json_not_corrupted(self):
        nested = {"nested": "object", "withSome": "fields", "that": "makes", "it": "longer than fifty characters total"}
        embedded = json.dumps(nested)
        assert len(embedded) > 50
        out = json.loads(JSONCompressor().compress(json.dumps({"payload": embedded})).compressed)
        assert out["payload"] == embedded and json.loads(out["payload"]) == nested

    def test_plain_long_string_truncated_and_fake_json_truncated(self):
        out = json.loads(JSONCompressor().compress(json.dumps({"payload": "a" * 80})).compressed)
        assert "...[compressed]..." in out["payload"] and len(out["payload"]) < 80
        fake = "{not actually json, just text that happens to start with a brace and is over fifty characters long"
        out = json.loads(JSONCompressor().compress(json.dumps({"payload": fake})).compressed)
        assert "...[compressed]..." in out["payload"]

    def test_preserve_fields_verbatim_even_if_embedded_json(self):
        embedded = json.dumps({"nested": "object", "it": "longer than fifty characters total", "x": "y" * 10})
        out = json.loads(JSONCompressor().compress(json.dumps({"id": embedded})).compressed)
        assert out["id"] == embedded

    def test_nested_array_policies(self):
        items = [{"description": "x" * 200, "index": i} for i in range(3)]
        out = json.loads(JSONCompressor().compress(json.dumps({"items": items})).compressed)
        assert len(out["items"]) == 3 and all(len(e["description"]) < 200 for e in out["items"])
        items = [{"description": "x" * 200, "index": i} for i in range(8)]
        out = json.loads(JSONCompressor().compress(json.dumps({"items": items})).compressed)
        assert len(out["items"]) == 6 and out["items"][3] == "..." and len(out["items"][0]["description"]) < 200

    def test_preserve_fields_inside_nested_arrays_and_top_level_sampling(self):
        long_id = "id-" * 30
        items = [{"id": long_id, "description": "x" * 200, "index": i} for i in range(8)]
        out = json.loads(JSONCompressor().compress(json.dumps({"items": items})).compressed)
        assert all(e["id"] == long_id for e in out["items"] if not isinstance(e, str))
        top = [{"id": long_id, "index": i} for i in range(20)]
        r = JSONCompressor().compress(json.dumps(top))
        assert r.strategy == "sampled_15"
        assert all(e["id"] == long_id for e in json.loads(r.compressed) if e != "...")

    def test_array_of_arrays_bounded(self):
        top = [[f"item{i}" for i in range(100)] for _ in range(20)]
        r = JSONCompressor().compress(json.dumps(top))
        assert r.strategy == "sampled_15"
        assert all(len(e) <= 6 for e in json.loads(r.compressed) if e != "...")

    def test_array_thresholds(self):
        r = JSONCompressor().compress(json.dumps(list(range(15))))
        assert (r.strategy, r.original_items, r.compressed_items) == ("full", 15, 15)
        r = JSONCompressor().compress(json.dumps(list(range(20))))
        parsed = json.loads(r.compressed)
        assert r.strategy == "sampled_15" and parsed[7] == "..."
        assert JSONCompressor.__init__ and JSONCompressor(__import__("app.compression.compressors.json_c", fromlist=["x"]).JSONCompressorConfig(max_items=0)).compress(json.dumps(list(range(50)))).strategy == "full"

    def test_non_ascii_not_escaped(self):
        assert "żółć" in JSONCompressor().compress(json.dumps({"k": "żółć"}, ensure_ascii=False)).compressed


# ============================================================ Log / Search / Simple
class TestLog:
    def test_keeps_errors_in_order_and_dedupes(self):
        lines = ["INFO boot"] + ["INFO heartbeat"] * 50 + ["ERROR disk failure"] + [f"INFO step {i}" for i in range(300)] + ["WARN slow"]
        r = LogCompressor().compress("\n".join(lines))
        out = r.compressed.split("\n")
        assert "[50x] INFO heartbeat" in out and "ERROR disk failure" in out and "WARN slow" in out
        assert out.index("ERROR disk failure") < out.index("WARN slow")  # original order kept
        assert any("log lines compressed" in ln for ln in out) and len(out) <= 110

    def test_timestamps_not_pinned_by_default(self):
        log = "\n".join(f"2024-05-01 10:{i // 60:02d}:{i % 60:02d} INFO tick {i}" for i in range(400))
        assert len(LogCompressor().compress(log).compressed.split("\n")) <= 101
        assert len(LogCompressor(LogCompressorConfig(pin_timestamps=True)).compress(log).compressed.split("\n")) == 400

    def test_short_log_unchanged(self):
        assert LogCompressor().compress("a\nb\nc").compressed == "a\nb\nc"


class TestSearch:
    def test_grouping_and_limits(self):
        lines = [f"pkg/file{f}.py:{n}:    x = {'a' * 300}" for f in range(60) for n in range(1, 30)]
        r = SearchCompressor().compress("\n".join(lines))
        assert r.original_matches == len(lines) and r.compressed_files <= 31
        assert "more files" in r.compressed and "more matches" in r.compressed and "...[truncated]" in r.compressed

    def test_implausible_matches_ignored(self):
        assert SearchCompressor().compress("12:30:45 started\nnote:7:thing").original_matches == 0  # no path separator / known extension
        assert SearchCompressor().compress("a.txt:5:hit\nlib/x:7:hit").original_matches == 2
        assert SearchCompressor().compress("src/a.py:999999:too large a line number").original_matches == 0


class TestSimple:
    def test_truncation_head_and_tail(self):
        text = "".join(f"line{i} " for i in range(500))
        r = SimpleCompressor().compress(text)
        assert r.strategy == "truncation" and r.compressed.startswith("line0") and r.compressed.endswith("line499 ") and "...[compressed]..." in r.compressed
        assert len(r.compressed) < len(text) * 0.4

    def test_short_passthrough_and_identifier_preservation(self):
        assert SimpleCompressor().compress("short").strategy == "passthrough"
        uid = "3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c"
        text = "alpha " * 300 + f" id {uid} " + "omega " * 300
        plain = SimpleCompressor().compress(text).compressed
        assert uid not in plain
        kept = SimpleCompressor(SimpleCompressorConfig(preserve_identifiers=True)).compress(text).compressed
        assert uid in kept


# ============================================================ Mask
def test_mask_and_entropy():
    m = StructureMask(["a", "b", "c", "d"], [True, True, False, True])
    assert [(s.start, s.end, s.is_structural) for s in mask_to_spans(m)] == [(0, 2, True), (2, 3, False), (3, 4, True)]
    assert m.preservation_ratio == 0.75 and m.compressible_count == 1
    assert m.union(StructureMask.full(m.tokens)).mask == [True] * 4
    assert m.intersection(StructureMask.empty(m.tokens)).mask == [False] * 4
    with pytest.raises(ValueError):
        StructureMask(["a"], [True, False])
    assert compute_entropy("aaaa") == 0 and compute_entropy("") == 0 and compute_entropy("abcdefgh") == pytest.approx(1.0)
    em = compute_entropy_mask(["short", "a1B2c3D4e5F6", "aaaaaaaaaaaa"])
    assert em.mask == [False, True, False]
    assert apply_mask_to_text("x" * 100, StructureMask(list("x" * 100), [False] * 100), lambda t: "C") == "C"


# ============================================================ CompressionCache (ported cache.test.ts)
class TestCache:
    def test_content_hash(self):
        h = CompressionCache.content_hash("hello world")
        assert re.fullmatch(r"[0-9a-f]{32}", h) and h == content_hash("hello world") and h != content_hash("goodbye world")

    def test_round_trip(self):
        c = CompressionCache()
        h = content_hash("original content")
        c.store_compressed(h, "compressed!", 10, len("original content"))
        assert c.get_compressed(h, len("original content")) == "compressed!"
        assert c.get_compressed(h) == "compressed!"
        assert c.get_compressed("deadbeef") is None and c.stats()["misses"] == 1

    def test_length_guard_keeps_entry(self):
        c = CompressionCache()
        c.store_compressed("g", "v", 5, 100)
        assert c.get_compressed("g", 999) is None and c.stats()["misses"] == 1
        assert c.get_compressed("g", 100) == "v" and c.stats()["hits"] == 1

    def test_unguarded_entries_never_trigger_guard(self):
        c = CompressionCache()
        c.store_compressed("u", "v", 5)
        assert c.get_compressed("u", 12345) == "v" and c.get_compressed("u", 0) == "v" and c.stats()["misses"] == 0

    def test_lru_eviction_and_clear(self):
        c = CompressionCache(max_entries=2)
        c.store_compressed("a", "1", 1); c.store_compressed("b", "2", 1)
        c.get_compressed("a")  # refresh a
        c.store_compressed("c", "3", 1)
        assert c.get_compressed("b") is None and c.get_compressed("a") == "1"
        assert c.stats()["tokens_saved"] == 2 and c.clear() == 2 and c.stats()["entries"] == 0


def test_tool_message_helpers():
    assert is_tool_result_message({"role": "tool"}) and is_tool_result_message({"role": "function"}) and not is_tool_result_message({"role": "user"})
    assert extract_tool_result_contents({"role": "tool", "content": "abc"}) == [(-1, "abc")]
    parts = {"role": "tool", "content": [{"type": "text", "text": "one"}, {"type": "image_url", "image_url": {}}, {"type": "text", "text": "two"}]}
    assert extract_tool_result_contents(parts) == [(0, "one"), (2, "two")]
    swapped = swap_tool_result_content_at(parts, 2, "X")
    assert swapped["content"][2]["text"] == "X" and parts["content"][2]["text"] == "two"  # input untouched


# ============================================================ Aligner
def test_volatile_detection():
    assert classify_token("123e4567-e89b-12d3-a456-426614174000") == "uuid"
    assert classify_token("2024-05-01T10:20:30Z") == "iso8601" and classify_token("2024-05-01") == "iso8601"
    assert classify_token("2024-13-45") is None
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert classify_token(jwt) == "jwt"
    assert classify_token("d41d8cd98f00b204e9800998ecf8427e") == "hex_hash" and classify_token("sk-abcdefghijklmnop1234") == "api_key"
    assert classify_token("hello") is None
    f = detect_volatile_content("Request id: 123e4567-e89b-12d3-a456-426614174000, started 2024-05-01T10:20:30Z.")
    assert sorted(x.label for x in f) == ["iso8601", "uuid"]
    assert all("..." in x.sample or len(x.sample) <= 16 for x in f)


def test_alignment_score():
    msgs = [{"role": "system", "content": "You are helpful."}, {"role": "user", "content": "id 123e4567-e89b-12d3-a456-426614174000"}]
    assert compute_alignment_score(msgs) == 100  # only system messages are scanned
    msgs[0]["content"] = "now 2024-05-01T10:20:30Z session 123e4567-e89b-12d3-a456-426614174000"
    assert compute_alignment_score(msgs) == 80
    a = analyze_alignment(msgs)
    assert a["findings"] == 2 and a["labels"] == {"iso8601": 1, "uuid": 1} and len(a["prefix_hash"]) == 12
    assert compute_alignment_score([{"role": "system", "content": "x 2024-05-01 " * 20}]) == 0  # clamps at 0
    assert compute_alignment_score([{"role": "system", "content": [{"type": "text", "text": "t 2024-05-01"}]}]) == 90


# ============================================================ Context analysis
def test_context_composition():
    body = {"messages": [
        {"role": "system", "content": "S" * 700},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "R" * 2000},
    ], "tools": [{"type": "function", "function": {"name": "read_file", "description": "d" * 600}}]}
    a = analyze_context(body)
    assert a["system_chars"] == 700 and a["tools_chars"] > 600 and a["messages_chars"] >= 2000
    kinds = {b["kind"]: b for b in a["blocks"]}
    assert kinds["tool_result"]["label"] == "read_file" and kinds["tool_result"]["chars"] == 2000 and "tools" in kinds and "system" in kinds
    assert "R" * 100 not in json.dumps(a)  # content is never stored, only sizes/labels/hashes
    assert analyze_context(body)["blocks"][0]["hash"] == a["blocks"][0]["hash"]  # stable hash for re-sent blocks


# ============================================================ Pipeline (ported compression-step.test.ts, chat format)
def big_json(seed=0):
    return json.dumps({"items": [{"id": i + seed, "name": f"item-{i}", "description": "a" * 50} for i in range(20)]}, indent=2)


def convo(tool_content, trailing=True):
    msgs = [{"role": "user", "content": "please read the config file"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": tool_content}]
    if trailing:
        msgs += [{"role": "assistant", "content": "here is the file content"}, {"role": "user", "content": "thanks, what next?"}]
    return msgs


@pytest.fixture
def fresh():
    return ContentRouter(), CompressionCache()


def test_pipeline_off_and_single_turn_noop(fresh):
    router, cache = fresh
    m = convo(big_json())
    assert pipeline.apply_compression(m, router, "off", cache).events == []
    r = pipeline.apply_compression([{"role": "user", "content": "hello there"}], router, "on", cache)
    assert r.events == [] and not r.mutated


def test_pipeline_compresses_older_tool_result_and_records_miss(fresh):
    router, cache = fresh
    content = big_json()
    msgs = convo(content)
    before = copy.deepcopy(msgs)
    r = pipeline.apply_compression(msgs, router, "on", cache)
    assert r.mutated and r.applied and msgs == before  # input never mutated
    assert len(r.messages[2]["content"]) < len(content)
    assert r.messages[0] == msgs[0] and r.messages[1] == msgs[1] and r.messages[3:] == msgs[3:]  # nothing else touched
    ev = r.events[0]
    assert len(r.events) == 1 and ev["cache_hit"] is False and ev["compressor_used"] == "JSONCompressor" and ev["content_type"] == "json" and ev["applied"] is True
    assert cache.get_compressed(content_hash(content)) is not None and r.tokens_saved > 0


def test_pipeline_cache_hit_reuses_and_skips_router(fresh):
    router, cache = fresh
    content = big_json()
    calls = []
    orig = router.compress
    router.compress = lambda c: calls.append(c) or orig(c)
    pipeline.apply_compression(convo(content), router, "on", cache)
    r2 = pipeline.apply_compression(convo(content), router, "on", cache)
    assert len(calls) == 1  # second turn: no recompression
    ev = r2.events[0]
    assert ev["cache_hit"] is True and ev["compressor_used"] == "cache_hit" and ev["content_type"] == "json" and ev["strategy"] is None  # deviation: hits keep the original content type
    assert r2.messages[2]["content"] == pipeline.apply_compression(convo(content), router, "on", cache).messages[2]["content"]  # deterministic prefix


def test_pipeline_live_zone_never_touched_then_compressed_next_turn(fresh):
    router, cache = fresh
    content = big_json()
    turn1 = convo(content, trailing=False)  # tool result is in the live zone (after the last assistant message)
    r1 = pipeline.apply_compression(turn1, router, "on", cache)
    assert not r1.mutated and r1.events == [] and cache.get_compressed(content_hash(content)) is None
    r2 = pipeline.apply_compression(convo(content, trailing=True), router, "on", cache)  # next turn: no longer live
    assert r2.mutated and len(r2.events) == 1 and r2.events[0]["cache_hit"] is False


def test_pipeline_parallel_results_and_part_lists(fresh):
    router, cache = fresh
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}}, {"id": "b", "type": "function", "function": {"name": "g", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": big_json(0)},
            {"role": "tool", "tool_call_id": "b", "content": [{"type": "text", "text": big_json(100)}, {"type": "text", "text": "tiny"}]},
            {"role": "function", "name": "legacy", "content": big_json(200)},
            {"role": "assistant", "content": "done"}, {"role": "user", "content": "ok"}]
    r = pipeline.apply_compression(msgs, router, "on", cache)
    assert len(r.events) == 3  # the tiny part produces no event
    assert all(len(r.messages[i]["content"] if isinstance(r.messages[i]["content"], str) else r.messages[i]["content"][0]["text"]) < len(big_json()) for i in (2, 3, 4))
    assert r.messages[3]["content"][1]["text"] == "tiny"


def test_pipeline_shadow_measures_but_leaves_messages(fresh):
    router, cache = fresh
    msgs = convo(big_json())
    r = pipeline.apply_compression(msgs, router, "shadow", cache)
    assert r.messages is msgs and not r.applied and not r.mutated
    assert len(r.events) == 1 and r.events[0]["applied"] is False and r.tokens_saved > 0


def test_pipeline_never_longer_and_only_tool_roles(fresh):
    router, cache = fresh
    # pretty JSON that the compressor cannot shrink beyond minification still must never grow
    hard = json.dumps({"a": "b" * 10}, indent=2) + " " * 120
    msgs = [{"role": "system", "content": big_json()}, {"role": "user", "content": big_json()},
            {"role": "assistant", "content": big_json(), "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": hard}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "x"}]
    r = pipeline.apply_compression(msgs, router, "on", cache)
    assert len(r.messages[3]["content"]) <= len(hard)
    assert [m["content"] for m in r.messages[:3]] == [m["content"] for m in msgs[:3]]  # system/user/assistant text untouched


def test_pipeline_get_router_caches_by_config():
    a = pipeline.get_router({"min_content_chars": 5})
    assert a is pipeline.get_router({"min_content_chars": 5}) and a is not pipeline.get_router({"min_content_chars": 6})
    assert a.c.min_content_chars == 5


# ============================================================ Insights math (fresh-only contract)
def test_aggregate_rows_contract():
    rows = aggregate_compression_rows([
        {"key": "json", "events": 10, "cache_hit_events": 6, "tokens_saved": 400, "sum_ratio": 1.2, "ratio_count": 4},
        {"key": None, "events": 2, "cache_hit_events": 0, "tokens_saved": 900, "sum_ratio": 0.5, "ratio_count": 2},
        {"key": "log", "events": 0, "cache_hit_events": 0, "tokens_saved": 400, "sum_ratio": 0, "ratio_count": 0}])
    assert [r["key"] for r in rows] == ["Unknown", "json", "log"]  # by tokensSaved desc, then key asc
    assert rows[1]["avgRatio"] == pytest.approx(0.3) and rows[1]["cacheHitRate"] == pytest.approx(0.6) and rows[2]["avgRatio"] is None and rows[2]["cacheHitRate"] == 0.0


def test_build_responses():
    r = build_compression_response(range_="24h", requests=5, events=20, cache_hit_events=5, applied_events=15, fresh_tokens_saved=1000, sum_ratio=3.0,
                                   ratio_count=10, tokens_not_sent=4000, potential_not_sent=300, avg_alignment=90.0, by_content_type=[], by_compressor=[],
                                   alignment={}, live_cache=None)
    t = r["totals"]
    assert t["cacheHitRate"] == 0.25 and t["avgCompressionRatio"] == 0.3 and t["shadowEvents"] == 5 and t["uniqueTokensSaved"] == 1000 and t["tokensNotSent"] == 4000
    rows = [{"ts": 1000 + i * 60, "session_key": "h:s1", "prompt_tokens": 100 * (i + 1), "completion_tokens": 10, "ctx_system_chars": 100, "ctx_tools_chars": 50,
             "ctx_messages_chars": 1000 * (i + 1), "ctx_blocks": [{"hash": "abc", "kind": "tool_result", "label": "read", "chars": 2000}]} for i in range(3)]
    rows.append({"ts": 1000 + 99999, "session_key": "h:s1", "prompt_tokens": 5, "completion_tokens": 1, "ctx_system_chars": 1, "ctx_tools_chars": 0, "ctx_messages_chars": 1, "ctx_blocks": None})
    c = build_context_response(rows, range_="24h", top=5)
    assert c["composition"]["systemChars"] == 301 and c["tokenTotals"]["prompt"] == 605 and abs(sum(c["composition"]["shares"].values()) - 1) < 1e-9
    assert c["contributors"][0]["occurrences"] == 3 and c["contributors"][0]["totalChars"] == 6000
    assert len(c["growth"]) == 2 and c["growth"][0]["requests"] == 1 and c["growth"][1]["requests"] == 3  # split by the 30 min gap, newest first
    assert build_context_response([], range_="1h")["composition"]["shares"] == {"system": 0.0, "tools": 0.0, "messages": 0.0}
