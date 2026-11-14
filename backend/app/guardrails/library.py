"""Resolves the content filters a gateway model picked (single filters and groups) into concrete rules."""
import time
from typing import Optional

from sqlmodel import select

_cache: dict = {}
_TTL = 5.0  # safety net only; every library write calls invalidate()


def invalidate() -> None:
    _cache.clear()


def resolve(cfg: Optional[dict]) -> list:
    """Enabled rules (dicts with id, name, pattern, stage, action) for cfg["filters"]; unknown ids are ignored."""
    if not cfg:
        return []
    f = cfg.get("filters") or {}
    fids, gids = tuple(sorted(set(f.get("filter_ids") or []))), tuple(sorted(set(f.get("group_ids") or [])))
    if not fids and not gids:
        return []
    key = (fids, gids)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    from ..db import session
    from ..models import ContentFilter, FilterGroupMember

    with session() as s:
        ids = set(fids)
        if gids:
            ids |= {m.filter_id for m in s.exec(select(FilterGroupMember).where(FilterGroupMember.group_id.in_(gids))).all()}
        rows = s.exec(select(ContentFilter).where(ContentFilter.id.in_(ids), ContentFilter.enabled == True)).all() if ids else []  # noqa: E712
        rules = [{"id": r.id, "name": r.name, "pattern": r.pattern, "stage": r.stage, "action": r.action, "validator": r.validator} for r in sorted(rows, key=lambda r: r.id)]
    _cache[key] = (time.time(), rules)
    return rules


def missing_refs(s, cfg: Optional[dict]) -> list:
    """Human-readable list of filter/group ids in cfg that do not exist (for validating a gateway save)."""
    from ..models import ContentFilter, FilterGroup

    f = (cfg or {}).get("filters") or {}
    out = [f"filter {i}" for i in f.get("filter_ids") or [] if not s.get(ContentFilter, i)]
    out += [f"group {i}" for i in f.get("group_ids") or [] if not s.get(FilterGroup, i)]
    return out
