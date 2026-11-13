# SmartGateway

Smart router for OpenAI-compatible LLM endpoints, with a web UI.

- **Endpoints** – add any OpenAI-compatible server (optional API key, stored encrypted). Models are discovered via `/models`;
  capabilities (`chat, reasoning, vision, audio, video, ocr, embedding, tools, tts, image_gen`), context window and slot count are
  detected from listing metadata, llama.cpp `/props`, Ollama `/api/show`, name heuristics and optional active probes. Everything is editable.
- **Smart gateways** – combine upstream models behind one URL `http://HOST:PORT/gw/<slug>/v1` with your own public model names.
  Surfaces: `chat/completions`, `completions`, `embeddings`, `audio/transcriptions|speech`, `images/generations|edits`, `models`,
  plus **Anthropic `/v1/messages`** and **OpenAI `/v1/responses`** (translated onto the same routing pipeline).
- **Routing** – capability matching from the request (image → vision, audio, tools, reasoning params, `X-SG-Require` header), **context-fit**
  (prompt size vs each model's window, with retry on overflow), slot-aware scheduling with a fair FIFO queue, **live slot sync**
  (llama.cpp `/slots`, vLLM `/metrics`) so other clients' load counts, strategies: least busy, weighted round-robin, priority failover,
  fastest, random, **tiered** (small→large by prompt complexity), **fallback members**, **sticky sessions** (`X-SG-Session` / `user`),
  retries and circuit breaker.
- **Multi-tenant** – per-gateway **API keys** (hashed, shown once) with RPM/TPM limits, concurrency cap, daily/monthly token quotas,
  allowed models and expiry; admin **login** (cookie session); upstream keys encrypted at rest (Fernet).
- **Request shaping** – optional per-model system prompt, default parameters and max-tokens ceiling (pass-through unless set),
  exact-match response cache (per model, optional Redis backend — see below).
- **UI** – dashboard, live animated flow graph per gateway, **Playground** (streaming chat, images, reasoning, routing info),
  gateway **Test** dialog, request log.

## Run
```bash
docker compose up -d --build            # UI + API on http://localhost:8080  (SG_PORT=9090 to change)
docker compose --profile demo up -d     # plus 3 mock upstreams: http://mock-a:9000/v1, mock-b, mock-c
tools/load.sh http://localhost:8080 30  # demo traffic against a gateway with slug "main"
```
Environment: `SG_ADMIN_PASSWORD` (+ `SG_ADMIN_USER`, default `admin`) enables login · `SG_SECRET_KEY` (otherwise a key file is generated
in the data volume — back it up together with the database) · `SG_CORS_ORIGINS` (browser clients of `/gw` and `/v1`) ·
`SG_SSL_CERT`/`SG_SSL_KEY` (serve HTTPS directly; otherwise put a reverse proxy in front) · `SG_DEFAULT_SLOTS`, `SG_HEALTH_INTERVAL`,
`SG_FALLBACK_GRACE`. Data lives in the `sg-data` volume (SQLite); schema upgrades are applied automatically on start.

Back up the data volume as a whole: the database is only usable together with `secret.key` (or your `SG_SECRET_KEY`); without it stored upstream keys must be re-entered.

Notes: gateway API keys cannot be recovered after creation (create a new one). Tiered routing and the complexity score are heuristics.
The context estimate is ~3.5 characters per token.

## Unified endpoint

Settings -> **Unified endpoint** exposes several gateways through one OpenAI-compatible URL, `/unified/v1`
(`chat/completions`, `completions`, `embeddings`, `models`, plus the Anthropic `messages` and `responses` surfaces).
You choose which gateways are exposed; the `model` a client sends is the **gateway name** (case-insensitive, the slug also works).
A gateway with several public models is reachable as `Name` (its configurable default model) and as `Name/model`.

Authentication: by default each gateway checks its own API key (open gateways need none). Create **unified API keys** in the same
card to protect the whole endpoint: with at least one key, every call needs a key (anonymous access is closed, even to open gateways).
A unified key works for all exposed gateways without their own keys (it carries no per-key limits or quotas); a gateway's own key still
works for that gateway only. Keys are stored hashed and the plaintext is shown once, at creation.
`GET /unified/v1/models` lists only what the caller may use, so protected gateways are not revealed to anonymous callers.
Routing, caching, compression, limits, quotas and logs all happen in the owning gateway, and responses report the gateway name as `model`.
It is off by default; the existing `/gw/<slug>/v1` and `/v1` URLs are unchanged.

## OpenCode Zen and OpenCode Go endpoints

Endpoints -> Add endpoint -> choose **OpenCode Zen** (pay-as-you-go, `https://opencode.ai/inference/v1`) or **OpenCode Go**
(subscription, `https://opencode.ai/inference/go/v1`) and paste your API key from the OpenCode console.

- **Catalog, not probes.** Models, capabilities (tools, reasoning, vision) and context windows come straight from OpenCode's `/models`
  metadata. No test requests are sent, so adding it costs nothing and does not touch your Go allowance. Re-scan refreshes the catalog.
- **Key check.** The catalog is public, so the key is verified with one 1-token request to a model listed as free; a rejected key is not saved.
- **Go requirements handled.** Go refuses requests without an `x-opencode-session` header. The gateway sends one id per conversation
  (`X-SG-Session` header, else `user`, else a hash of the system prompt + first user message), plus a `SmartGateway/<version>` user agent.
- **Concurrency.** Hosted models default to 8 concurrent requests (`SG_HOSTED_SLOTS`); adjust per model on the Endpoints page.
- **Limits.** Go allowances (5 h / weekly / monthly) surface as HTTP 429: add a second member as a fallback in the gateway.
  Stress tests on these endpoints spend real credits or allowance, and the dialog warns about it.
- A manually typed `opencode.ai/inference` or `opencode.ai/zen` URL is recognised automatically.

## Deploying on another host

`deploy/` holds a standalone compose file (no source needed on the target):

```bash
./deploy/build-image.sh linux/amd64     # or linux/arm64 - match the target host's CPU
# copy dist-deploy/ to the target, then there:
gunzip -c smartgateway-image.tar.gz | docker load
cp .env.example .env                    # set SG_ADMIN_PASSWORD, SG_PORT, ...
docker compose up -d                    # add --profile redis for the Redis cache
```

## Context compression
Long agent conversations resend every earlier tool output on every turn. **Context compression** shrinks those *older* tool results
(`role: "tool"` messages, including Anthropic `tool_result` blocks) before they reach the model; user, assistant and system text is never
touched, and the newest results (everything after the last assistant message) are left intact. Content is classified (JSON, logs, search
output, diffs, code, text) and routed to a compressor: JSON arrays are sampled, long strings trimmed, logs keep their error lines and
collapse repeats, grep output keeps paths and line numbers, everything else is truncated head+tail. Results are cached by content hash,
so later turns send a byte-identical (prompt-cache friendly) prefix, and a result is only used if it is shorter than the original.
Compressing before routing also lets long conversations fit smaller context windows.

* **Modes:** `off` (default) · `shadow` (measure what it would save, forward the original) · `on`. Set the global default in
  **Settings → Context compression**; each public model can override it in the gateway editor. A client can opt out per request with
  `X-SG-Compress: off`. Compressed requests carry `X-SG-Compressed: <tokens saved>`.
* **Compression page:** savings by content type and compressor (unique savings vs. tokens not sent on every turn), a **cache-alignment**
  view (volatile content such as timestamps/UUIDs in system prompts lowers a 0–100 score and breaks prefix/KV-cache reuse), **context
  composition** (what fills the context, top repeated blocks, growth per session) and a **Try it** tool to preview compression on pasted text.
  Only sizes, labels and hashes are recorded, never request content.
* Compression is lossy (markers replace dropped parts): evaluate with `shadow` first.

Credits: the compression algorithms are ported from [TinyClaude](https://github.com/ALange/TinyClaude), which itself builds on
[Headroom](https://github.com/headroomlabs-ai/headroom). Small deviations: logs keep their original order and timestamped lines are not
auto-pinned, volatile-token detection strips only surrounding punctuation, results that are not shorter are discarded.

## Stress test
Each gateway card has a **Stress test** button that measures how much a gateway can deliver. It ramps up the number of simultaneous
streaming requests in stages (profiles: Quick `1,2,4,8`, Standard `…16`, Thorough `…64`, or custom levels) and reports per stage the total
and per-request tokens/s, prompt-processing speed, time to first token, queue wait, latency and the spread over upstream servers. The
summary gives the **peak throughput** (lowest concurrency within 2% of the best), the **single-stream speed**, the **comfortable
concurrency** (each request keeps ≥ 70% of its single-stream speed and does not queue) and where **saturation** starts, next to the
configured slot count. The test runs on the server through the real routing pipeline with unique prompts; the response cache and
compression are bypassed and the synthetic requests are not written to the request log or the dashboard. It stops early when requests start
failing or throughput falls past its peak, can be stopped at any time, only one test runs at a time, and past runs are kept for comparison
(last 20 per gateway). It sends real load to your upstream servers and needs an explicit confirmation.

## Response cache and optional Redis
Caching is configured **per public model** in the gateway editor (on/off, TTL, whether non-zero-temperature requests are cached too,
and whether **streaming requests** are cached). Cached: `chat/completions` and `embeddings`; send `X-SG-Cache: 0` to bypass or
`X-SG-Cache: 1` to force caching of one request. Responses carry `X-SG-Cache: hit|miss|skip|bypass`.

**Streaming** (opt-in per model, "Also cache streaming requests"): a stream is cached as one aggregated response once it has
completed cleanly, and replayed as a fresh SSE stream on later hits (`X-SG-Replay: instant|realistic`; *realistic* paces the replay over
the original generation time, capped at 10 s). Because streaming and non-streaming requests share the same cache key, an answer stored
by a stream also serves non-streaming clients and vice versa; the Anthropic `/v1/messages` and Responses surfaces benefit as well.
Interrupted streams (client disconnect, upstream error), `n > 1`, `logprobs` requests and legacy `completions` streams are never cached.
Replayed chunks are re-split on word boundaries, so chunk boundaries differ from the original stream; the content, tool calls, reasoning,
finish reason and usage are identical. The Playground's **Test cache** button demonstrates all of this.

By default entries live in process memory (lost on restart). To keep them across restarts and share them between instances, open
**Settings** and enter a Redis URL (`redis://[:password@]host:6379/0`, `rediss://` for TLS), press *Test connection*, enable and save —
no restart needed. A bundled Redis is available with `docker compose --profile redis up -d` (URL `redis://redis:6379/0`); `SG_REDIS_URL`
can preconfigure it. The URL (with its password) is stored encrypted. If Redis is unreachable requests keep working: the cache falls back
to memory (switchable in Settings) and reconnects automatically (`SG_CACHE_RETRY_AFTER`, default 15 s). Cached responses are shared by all
API keys of a gateway.

## Dev / tests
`cd backend && pip install -r requirements.txt && pytest` (Python 3.11–3.12) · `cd frontend && npm i && npm run dev` ·
`python tools/e2e_stress.py` covers the stress test · `python tools/e2e_stream_cache.py` covers streaming cache · `python tools/e2e_compression.py` covers compression/alignment/insights · `python tools/e2e_redis.py` (needs Docker; starts temporary Redis containers) covers the cache/Redis scenarios · `python tools/e2e_v2.py` runs an end-to-end suite against throw-away mock upstreams and a temporary gateway (never touches your data).

## Security notes for contributors

- Never commit `.env`, `secret.key`, `*.db`, backups or exported images (all are in `.gitignore`). Use `.env.example` as the template.
- Set `SG_ADMIN_PASSWORD` in any deployment reachable from a network; without it the UI and management API are open.
- Back up `secret.key` (or keep `SG_SECRET_KEY` in your secret manager) together with the database.
