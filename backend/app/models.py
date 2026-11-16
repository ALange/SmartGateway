import time
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .crypto import EncryptedString


class Endpoint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    base_url: str
    api_key: Optional[str] = Field(default=None, sa_column=Column(EncryptedString))
    slot_source: Optional[str] = None  # slots | vllm | none (None = not checked yet)
    provider: Optional[str] = None  # opencode-zen | opencode-go (hosted catalogs: metadata-only discovery, session header) | None
    status: str = "unknown"  # online | offline | unknown
    last_error: Optional[str] = None
    last_checked: Optional[float] = None
    created_at: float = Field(default_factory=time.time)


class Model(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    endpoint_id: int = Field(foreign_key="endpoint.id", index=True)
    upstream_id: str
    capabilities: list = Field(default_factory=list, sa_column=Column(JSON))
    cap_sources: dict = Field(default_factory=dict, sa_column=Column(JSON))
    max_slots: int = 4
    context_length: Optional[int] = None
    enabled: bool = True


class Gateway(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    api_key: Optional[str] = None  # legacy (v1); migrated into ApiKey rows
    strategy: str = "least_busy"
    cache_mode: str = "off"  # off | exact
    queue_timeout: float = 60.0
    max_retries: int = 2
    created_at: float = Field(default_factory=time.time)
    kind: str = "router"  # router | council (see council.py)
    council: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    anthropic_compat: bool = False  # unused: every gateway speaks both APIs (kept so existing databases stay valid)


class GatewayModel(SQLModel, table=True):
    """A public model name exposed by a gateway."""
    id: Optional[int] = Field(default=None, primary_key=True)
    gateway_id: int = Field(foreign_key="gateway.id", index=True)
    public_name: str
    strategy: Optional[str] = None  # overrides gateway strategy
    sticky: bool = False
    system_prompt: Optional[str] = None
    max_tokens_cap: Optional[int] = None
    default_params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    cache_enabled: bool = False
    cache_ttl: Optional[int] = None  # seconds; None = global default
    cache_any_temperature: bool = False  # False: only temperature == 0 (or X-SG-Cache: 1)
    cache_streaming: bool = False  # also cache/replay stream: true requests
    cache_replay_speed: str = "instant"  # instant | realistic (replay at the originally measured speed)
    compress_mode: Optional[str] = None  # None = inherit global setting; off | shadow | on
    guardrails: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # None = all guardrails off (see guardrails/pipeline.py)


class Member(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    gateway_model_id: int = Field(foreign_key="gatewaymodel.id", index=True)
    model_id: int = Field(foreign_key="model.id")
    weight: int = 1
    priority: int = 0  # lower = preferred
    slots_override: Optional[int] = None
    enabled: bool = True
    is_fallback: bool = False
    tier: int = 2  # 1 small/fast .. 3 large (used by the "tiered" strategy)


class ApiKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    gateway_id: int = Field(foreign_key="gateway.id", index=True)
    name: str
    key_hash: str = Field(index=True)
    key_prefix: str = ""
    enabled: bool = True
    expires_at: Optional[float] = None
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None
    daily_token_quota: Optional[int] = None
    monthly_token_quota: Optional[int] = None
    max_concurrent: Optional[int] = None
    allowed_models: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    last_used: Optional[float] = None


class RequestLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    gateway_id: int = Field(index=True)
    key_id: Optional[int] = Field(default=None, index=True)
    cached: bool = False
    public_model: str
    endpoint_id: Optional[int] = None
    model_id: Optional[int] = None
    upstream_model: Optional[str] = None
    route: str = ""
    requirements: str = ""
    status: int = 0
    ok: bool = True
    stream: bool = False
    queue_ms: int = 0
    ttft_ms: Optional[int] = None
    total_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: int = 1
    error: Optional[str] = None
    finish_reason: Optional[str] = None   # what the model reported: stop | length | tool_calls | content_filter | None (no finish chunk)
    outcome: Optional[str] = Field(default=None, index=True)  # complete | cut_by_limit | cut_in_reasoning | no_answer | truncated | client_aborted | stream_error | filtered | empty
    # context compression / cache alignment / composition (all nullable; no request content is stored)
    cache_state: Optional[str] = None  # hit | miss | skip | bypass (None = request was not eligible for caching)
    compress_mode: Optional[str] = None
    tokens_saved: Optional[int] = None
    alignment_score: Optional[int] = None
    volatile_findings: Optional[int] = None
    volatile_labels: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    prefix_hash: Optional[str] = None
    session_key: Optional[str] = Field(default=None, index=True)
    ctx_system_chars: Optional[int] = None
    ctx_tools_chars: Optional[int] = None
    ctx_messages_chars: Optional[int] = None
    ctx_blocks: Optional[list] = Field(default=None, sa_column=Column(JSON))
    # guardrails (counts and rule names only, never request content)
    guardrail_action: Optional[str] = None  # pass | flagged | redacted | retried | failed | blocked
    guardrail_findings: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    output_retries: Optional[int] = None
    council_run_id: Optional[int] = None


class ContentFilter(SQLModel, table=True):
    """A reusable regex content filter. Gateway models reference filters (directly or through groups) in their guardrails config."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    pattern: str
    stage: str = "both"  # input | output | both
    action: str = "block"  # block | redact
    validator: Optional[str] = None  # name of a checksum validator (guardrails/checks.py); only valid numbers match
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)


class FilterGroup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""


class FilterGroupMember(SQLModel, table=True):
    group_id: int = Field(foreign_key="filtergroup.id", primary_key=True)
    filter_id: int = Field(foreign_key="contentfilter.id", primary_key=True)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""  # JSON


class CompressionEvent(SQLModel, table=True):
    """One compressed (or shadow-measured) tool-result block of one request."""
    id: Optional[int] = Field(default=None, primary_key=True)
    request_log_id: Optional[int] = Field(default=None, index=True)
    ts: float = Field(default_factory=time.time, index=True)
    gateway_id: int = Field(index=True)
    public_model: str = ""
    content_type: Optional[str] = None
    compressor_used: str = ""
    strategy: Optional[str] = None
    compression_ratio: Optional[float] = None
    tokens_before: int = 0
    tokens_after: int = 0
    chars_before: int = 0
    chars_after: int = 0
    cache_hit: bool = False
    applied: bool = True


class StressRun(SQLModel, table=True):
    """Result of one gateway stress test (kept so runs can be compared over time)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    gateway_id: int = Field(index=True)
    public_model: str = ""
    started_at: float = Field(default_factory=time.time, index=True)
    finished_at: Optional[float] = None
    status: str = "running"  # running | done | stopped | error
    stop_reason: Optional[str] = None
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))


class AuditEvent(SQLModel, table=True):
    """One successful change made through the admin API. Stores field names only, never values."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    actor: str = ""
    ip: str = ""
    action: str = Field(default="", index=True)
    target_type: str = Field(default="", index=True)
    target_id: str = ""
    method: str = ""
    path: str = ""
    status: int = 200
    fields: Optional[list] = Field(default=None, sa_column=Column(JSON))
    summary: str = ""


class CouncilRun(SQLModel, table=True):
    """One council request. Only stored when the council has store_runs on: it holds prompts and answers."""
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: float = Field(default_factory=time.time, index=True)
    gateway_id: int = Field(index=True)
    key_id: Optional[int] = None
    status: str = "running"  # running | done | failed
    question: str = ""
    details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    final: str = ""
    total_ms: int = 0
