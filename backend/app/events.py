"""In-process pub/sub for live UI events."""
import asyncio
import time
from collections import deque

_subscribers: set[asyncio.Queue] = set()
recent: deque = deque(maxlen=200)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict) -> None:
    event.setdefault("ts", time.time())
    recent.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
