"""Sticky routing: remember which member served a session so prompt caches get reused."""
import hashlib
import time
from collections import OrderedDict
from typing import Optional

TTL = 1800.0
MAX = 5000
_map: "OrderedDict[tuple, tuple[int, float]]" = OrderedDict()


def session_key(header: str, body: dict) -> str:
    if header:
        return "h:" + header
    if body.get("user"):
        return "u:" + str(body["user"])
    msgs = body.get("messages") or []
    sys_txt = next((m.get("content") for m in msgs if m.get("role") == "system"), "")
    first = next((m.get("content") for m in msgs if m.get("role") == "user"), "")
    return "p:" + hashlib.sha1((str(sys_txt) + "\x00" + str(first)).encode()).hexdigest()[:16]


def get(gateway_model_id: int, key: str) -> Optional[int]:
    k = (gateway_model_id, key)
    v = _map.get(k)
    if not v:
        return None
    if time.time() - v[1] > TTL:
        _map.pop(k, None)
        return None
    _map.move_to_end(k)
    return v[0]


def put(gateway_model_id: int, key: str, member_id: int) -> None:
    _map[(gateway_model_id, key)] = (member_id, time.time())
    _map.move_to_end((gateway_model_id, key))
    while len(_map) > MAX:
        _map.popitem(last=False)
