"""Gateway stress test runner.

Drives load through the real routing pipeline (`proxy.dispatch`, in-process) in stages of increasing concurrency, measures
each stage (see stress_metrics) and persists the result. Cache and compression are bypassed, prompts are unique, and the
synthetic requests are not written to the request log.
"""
import asyncio
import json
import time
import uuid
from collections import deque
from typing import Optional

from fastapi.responses import StreamingResponse
from sqlmodel import select

from . import stress_metrics as sm
from .db import session
from .models import Gateway, StressRun
from .runtime_ctx import no_request_log

KEEP_RUNS_PER_GATEWAY = 20
DRAIN_MIN_SECONDS = 30
SENTENCES = [
    "The scheduler assigns each incoming request to the least busy upstream model while respecting per-model slot limits.",
    "Compression shrinks older tool results before they are resent so that long conversations fit smaller context windows.",
    "A response cache can answer identical requests instantly, either from memory or from a shared Redis server.",
    "Streaming responses are delivered token by token, which lets clients show progress while the model is still generating.",
    "Operators watch throughput, time to first token and queueing to decide how many requests a server can handle at once.",
    "Prompt processing is compute bound and scales with the number of input tokens, while generation is memory bandwidth bound.",
    "When every slot is busy, new requests wait in a fair queue until a slot is released or the queue timeout expires.",
    "Health checks mark unreachable endpoints offline so that traffic fails over to the remaining members of a gateway.",
]


CODE_SNIPPETS = [
    '''def {n}_load_config(path):
    """Load a JSON configuration file and drop private keys."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if "version" not in raw:
        raise ValueError("configuration has no version field")
    return {{k: v for k, v in raw.items() if not k.startswith("_")}}
''',
    '''class {n}Cache:
    """Small LRU cache with a maximum number of entries."""

    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._data: "OrderedDict[str, object]" = OrderedDict()

    def get(self, key: str):
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, value: object) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)
''',
    '''async def {n}_fetch_with_retry(client, url, attempts=3, backoff=0.5):
    last_error = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(backoff * (2 ** attempt))
    raise RuntimeError(f"giving up on {{url}}") from last_error
''',
    '''@dataclass
class {n}Order:
    order_id: int
    customer: str
    lines: list[tuple[str, int, float]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(qty * price for _, qty, price in self.lines), 2)

    def add_line(self, sku: str, qty: int, price: float) -> None:
        if qty <= 0 or price < 0:
            raise ValueError("quantity must be positive and price non-negative")
        self.lines.append((sku, qty, price))
''',
    '''def {n}_merge_intervals(intervals):
    """Merge overlapping [start, end] intervals."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
''',
    '''def {n}_build_query(table, filters, limit=100):
    clauses, params = [], []
    for column, value in filters.items():
        clauses.append(f"{{column}} = ?")
        params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return f"SELECT * FROM {{table}}{{where}} LIMIT {{int(limit)}}", params
''',
    '''class {n}Tokenizer:
    PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\\d+|[^\\sA-Za-z0-9_]")

    def tokenize(self, text: str) -> list[str]:
        return self.PATTERN.findall(text)

    def count(self, text: str) -> int:
        return len(self.tokenize(text))
''',
]
CODE_CHARS_PER_TOKEN = 3  # source code is more token-dense than prose
PROSE_CHARS_PER_TOKEN = 4


def make_prompt(approx_tokens: int, nonce: str, scenario: str = "prose") -> str:
    """A unique prompt of roughly `approx_tokens` tokens. The nonce comes first (prose) or is part of every identifier (code),
    so upstream prefix/KV caches cannot make the prompt cheaper than a real one."""
    i = int(nonce[:4], 16) if nonce[:4].isalnum() else 0
    if scenario == "code":
        target = approx_tokens * CODE_CHARS_PER_TOKEN
        parts = [f"# project context (build {nonce})", "import asyncio, json, re", "from collections import OrderedDict", "from dataclasses import dataclass, field"]
        n, k = 0, 0
        while n < target - 260:
            snippet = CODE_SNIPPETS[(i + k) % len(CODE_SNIPPETS)].format(n=f"m{nonce[:6]}x{k}")
            parts.append(snippet)
            n += len(snippet) + 1
            k += 1
        parts.append("# Task: using the code above as context, write a new module that adds request batching and rate limiting to these helpers. "
                     "Include full type hints, docstrings and unit tests. Output only code, no explanations.")
        return "\n\n".join(parts)
    target = approx_tokens * PROSE_CHARS_PER_TOKEN
    parts = [f"[run {nonce}] Read the following background material carefully."]
    n = 0
    while n < target - 200:
        s = SENTENCES[i % len(SENTENCES)]
        parts.append(s)
        n += len(s) + 1
        i += 1
    parts.append("Using the material above, write a long, detailed, multi-paragraph analysis and keep writing at length.")
    return " ".join(parts)


class Job:
    def __init__(self, gateway: Gateway, cfg: dict, slots: Optional[int]):
        self.id = uuid.uuid4().hex[:10]
        self.gateway = gateway
        self.cfg = cfg
        self.slots = slots
        self.status = "running"  # running | done | stopped | error
        self.stop_reason: Optional[str] = None
        self.started = time.time()
        self.finished: Optional[float] = None
        self.stages: list[dict] = []
        self.stage_index = 0
        self.current: Optional[dict] = None
        self.live: deque = deque(maxlen=5000)  # (time, 1) per streamed content chunk
        self.summary: Optional[dict] = None
        self.error: Optional[str] = None
        self.run_id: Optional[int] = None
        self.task: Optional[asyncio.Task] = None
        self.stop_requested = False
        self._records: list[dict] = []
        self._inflight = 0

    def snapshot(self) -> dict:
        now = time.time()
        cur = None
        if self.current and self.status == "running":
            recent = sum(1 for t, _ in self.live if now - t <= 3.0)
            cur = {**self.current, "elapsed": now - self.current["t0"], "inflight": self._inflight,
                   "completed": sum(1 for r in self._records if r.get("ok")), "errors": sum(1 for r in self._records if not r.get("ok")),
                   "tps_now": recent / 3.0}
        return {"job_id": self.id, "gateway_id": self.gateway.id, "gateway": self.gateway.name, "model": self.cfg["model"], "status": self.status,
                "stop_reason": self.stop_reason, "config": self.cfg, "slots": self.slots, "started": self.started, "finished": self.finished,
                "elapsed": (self.finished or now) - self.started, "stage_index": self.stage_index, "stage_count": len(self.cfg["levels"]),
                "stages": self.stages, "current": cur, "summary": self.summary, "run_id": self.run_id, "error": self.error}


CURRENT: Optional[Job] = None
JOBS: dict[str, Job] = {}


def running() -> Optional[Job]:
    return CURRENT if CURRENT and CURRENT.status == "running" else None


async def _one_request(job: Job) -> dict:
    from .proxy import dispatch  # late import: proxy is heavy and must not import this module

    gw = job.gateway
    nonce = uuid.uuid4().hex[:12]
    scenario = job.cfg.get("scenario", "prose")
    body = {"model": job.cfg["model"], "stream": True, "temperature": 0.2 if scenario == "code" else 0.7, "max_tokens": job.cfg["max_tokens"],
            "messages": [{"role": "user", "content": make_prompt(job.cfg["prompt_tokens"], nonce, scenario)}]}
    rec: dict = {"start": time.time(), "first": None, "end": None, "ok": False, "status": None, "tokens": 0, "prompt_tokens": 0,
                 "queue_ms": 0, "upstream": None, "error": None}
    job._inflight += 1
    try:
        resp = await dispatch(gw, None, "chat/completions", body, None, {"x-sg-cache": "0", "x-sg-compress": "off"})
        rec["status"] = resp.status_code
        rec["queue_ms"] = int(resp.headers.get("x-sg-queue-ms") or 0)
        rec["upstream"] = resp.headers.get("x-sg-upstream")
        if isinstance(resp, StreamingResponse) and resp.status_code < 400:
            finish, chunks = False, 0
            async for part in resp.body_iterator:
                for ln in (part.decode() if isinstance(part, bytes) else part).splitlines():
                    if not ln.startswith("data:") or "[DONE]" in ln:
                        continue
                    try:
                        o = json.loads(ln[5:])
                    except ValueError:
                        continue
                    ch = (o.get("choices") or [None])[0]
                    if ch:
                        d = ch.get("delta") or {}
                        if d.get("content") or d.get("reasoning_content") or d.get("reasoning") or d.get("tool_calls"):
                            if rec["first"] is None:
                                rec["first"] = time.time()
                            chunks += 1
                            job.live.append((time.time(), 1))
                        if ch.get("finish_reason"):
                            finish = True
                    if o.get("usage"):
                        rec["tokens"] = int(o["usage"].get("completion_tokens") or 0)
                        rec["prompt_tokens"] = int(o["usage"].get("prompt_tokens") or 0)
            rec["tokens"] = rec["tokens"] or chunks
            rec["ok"] = finish
            if not finish:
                rec["error"] = "stream ended without a finish reason"
        else:
            try:
                rec["error"] = (json.loads(resp.body).get("error") or {}).get("message", "error")[:120]
            except Exception:  # noqa: BLE001
                rec["error"] = f"HTTP {resp.status_code}"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        job._inflight -= 1
        rec["end"] = time.time()
    return rec


async def _worker(job: Job, deadline: float, records: list) -> None:
    while time.time() < deadline and not job.stop_requested:
        records.append(await _one_request(job))
        if records[-1]["status"] and records[-1]["status"] >= 400 and not records[-1]["ok"]:
            await asyncio.sleep(0.2)  # fast failures must not turn into a tight retry loop


async def _run(job: Job) -> None:
    no_request_log.set(True)
    stage_s = job.cfg["stage_seconds"]
    tasks: list[asyncio.Task] = []
    try:
        for i, level in enumerate(job.cfg["levels"]):
            job.stage_index = i
            t0 = time.time()
            records: list[dict] = []
            job._records = records
            job.current = {"concurrency": level, "t0": t0, "stage_seconds": stage_s}
            tasks = [asyncio.create_task(_worker(job, t0 + stage_s, records)) for _ in range(level)]
            _, pending = await asyncio.wait(tasks, timeout=stage_s + max(DRAIN_MIN_SECONDS, stage_s * 2))
            for t in pending:  # requests that never finished within the drain period count as failures
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                records.append({"start": t0, "first": None, "end": time.time(), "ok": False, "status": "timeout", "tokens": 0, "queue_ms": 0})
            job.stages.append(sm.stage_metrics(records, level, t0, stage_s))
            reason = sm.should_stop(job.stages)
            if reason and i < len(job.cfg["levels"]) - 1:
                job.stop_reason = f"stopped early: {reason}"
                break
        job.status = "done"
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if job._records and job.current:  # keep what the interrupted stage measured
            job.stages.append(sm.stage_metrics(job._records, job.current["concurrency"], job.current["t0"], stage_s, interrupted=True))
        job.status = "stopped"
        job.stop_reason = job.stop_reason or "stopped by user"
    except Exception as e:  # noqa: BLE001
        job.status, job.error = "error", f"{type(e).__name__}: {e}"
    finally:
        job.finished = time.time()
        job.current = None
        job.summary = sm.analyze([s for s in job.stages if not s.get("interrupted") or s["completed"] > 0], job.slots)
        _persist(job)


def _persist(job: Job) -> None:
    try:
        with session() as s:
            run = s.get(StressRun, job.run_id) if job.run_id else None
            if run:
                run.finished_at, run.status, run.stop_reason = job.finished, job.status, job.stop_reason or job.error
                run.result = {"stages": job.stages, "summary": job.summary, "error": job.error}
                s.add(run)
                s.commit()
            old = s.exec(select(StressRun).where(StressRun.gateway_id == job.gateway.id).order_by(StressRun.started_at.desc())).all()
            for r in old[KEEP_RUNS_PER_GATEWAY:]:
                s.delete(r)
            s.commit()
    except Exception:  # noqa: BLE001
        pass


def start(gateway: Gateway, cfg: dict, slots: Optional[int]) -> Job:
    global CURRENT
    job = Job(gateway, cfg, slots)
    with session() as s:
        run = StressRun(gateway_id=gateway.id, public_model=cfg["model"], started_at=job.started, config={**cfg, "slots": slots})
        s.add(run)
        s.commit()
        s.refresh(run)
        job.run_id = run.id
    CURRENT = job
    JOBS[job.id] = job
    while len(JOBS) > 5:
        JOBS.pop(next(iter(JOBS)))
    job.task = asyncio.create_task(_run(job))
    return job


def stop(job: Job) -> None:
    job.stop_requested = True
    job.stop_reason = "stopped by user"
    if job.task and not job.task.done():
        job.task.cancel()
