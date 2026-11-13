import asyncio
import math
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import settings_store as st
from ..compression import pipeline
from ..compression.detector import detect_content_type
from ..compression.router import ContentRouter, ContentRouterConfig

router = APIRouter(prefix="/api")
MODES = ("off", "shadow", "on")
MAX_PREVIEW_CHARS = 1_000_000


class CompressionSettingsIn(BaseModel):
    mode: Optional[str] = None
    config: Optional[dict] = None
    context_insights_enabled: Optional[bool] = None


class PreviewIn(BaseModel):
    text: str
    config: Optional[dict] = None  # None = the saved configuration


def _check(cfg: ContentRouterConfig) -> None:
    def rng(name, v, lo, hi):
        if not (lo <= v <= hi):
            raise HTTPException(400, f"{name} must be between {lo} and {hi}")
    rng("min_content_chars", cfg.min_content_chars, 0, 100_000)
    rng("json.max_items", cfg.json.max_items, 0, 1000)
    rng("json.compression_ratio", cfg.json.compression_ratio, 0.05, 0.95)
    rng("log.max_lines", cfg.log.max_lines, 10, 10_000)
    rng("search.max_results", cfg.search.max_results, 1, 1000)
    rng("search.max_lines_per_file", cfg.search.max_lines_per_file, 1, 1000)
    rng("search.max_snippet_chars", cfg.search.max_snippet_chars, 20, 5000)
    rng("simple.compression_ratio", cfg.simple.compression_ratio, 0.05, 0.95)


def view() -> dict:
    s = st.get_all()
    return {"mode": s["compress_mode"], "config": ContentRouterConfig.from_dict(s["compress_config"]).to_dict(),
            "defaults": ContentRouterConfig().to_dict(), "context_insights_enabled": s["context_insights_enabled"],
            "cache": pipeline.get_cache().stats()}


@router.get("/settings/compression")
def get_compression_settings():
    return view()


@router.put("/settings/compression")
def put_compression_settings(body: CompressionSettingsIn):
    vals: dict = {}
    if body.mode is not None:
        if body.mode not in MODES:
            raise HTTPException(400, f"mode must be one of {MODES}")
        vals["compress_mode"] = body.mode
    if body.config is not None:
        cfg = ContentRouterConfig.from_dict(body.config)
        _check(cfg)
        vals["compress_config"] = cfg.to_dict()
    if body.context_insights_enabled is not None:
        vals["context_insights_enabled"] = body.context_insights_enabled
    st.update(vals)
    return view()


def _preview(text: str, cfg: ContentRouterConfig) -> dict:
    det = detect_content_type(text)
    r = ContentRouter(cfg).compress(text)
    saved = max(0, r.tokens_before - r.tokens_after)
    return {"detected": {"content_type": det.content_type.value, "confidence": det.confidence, "label": det.label, "language": det.language},
            "content_type": r.content_type.value, "compressor_used": r.compressor_used, "strategy": r.strategy,
            "compression_ratio": r.compression_ratio, "tokens_before": r.tokens_before, "tokens_after": r.tokens_after,
            "tokens_saved": saved, "chars_before": len(text), "chars_after": len(r.compressed), "was_mixed": r.was_mixed,
            "sub_results": r.sub_results, "compressed": r.compressed}


@router.post("/compression/preview")
async def preview(body: PreviewIn):
    if len(body.text) > MAX_PREVIEW_CHARS:
        raise HTTPException(413, f"Text too large for the preview (max {MAX_PREVIEW_CHARS} characters)")
    cfg = ContentRouterConfig.from_dict(body.config if body.config is not None else st.get_all()["compress_config"])
    _check(cfg)
    return await asyncio.to_thread(_preview, body.text, cfg)


@router.get("/compression/cache")
def cache_stats():
    return pipeline.get_cache().stats()


@router.post("/compression/cache/clear")
def cache_clear():
    return {"cleared": pipeline.get_cache().clear()}
