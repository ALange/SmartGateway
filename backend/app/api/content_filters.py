"""Content filter library: reusable regex filters and groups of filters, picked by gateway models."""
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..guardrails import checks, filters as rules, library
from ..models import ContentFilter, Gateway, GatewayModel, FilterGroup, FilterGroupMember

router = APIRouter(prefix="/api")


class FilterIn(BaseModel):
    name: str
    description: str = ""
    pattern: str
    stage: str = "both"
    action: str = "block"
    validator: Optional[str] = None
    enabled: bool = True
    group_ids: list[int] = []


class GroupIn(BaseModel):
    name: str
    description: str = ""
    filter_ids: list[int] = []


class TestIn(BaseModel):
    pattern: str
    text: str = ""
    validator: Optional[str] = None


def usage(s: Session) -> tuple:
    """(filter id -> users, group id -> users) where a user is 'gateway / public model' of a gateway model that references it."""
    by_filter: dict = {}
    by_group: dict = {}
    names = {g.id: g.name for g in s.exec(select(Gateway)).all()}
    for gm in s.exec(select(GatewayModel)).all():
        f = ((gm.guardrails or {}).get("filters") or {})
        label = f"{names.get(gm.gateway_id, '?')} / {gm.public_name}"
        for i in f.get("filter_ids") or []:
            by_filter.setdefault(i, []).append(label)
        for i in f.get("group_ids") or []:
            by_group.setdefault(i, []).append(label)
    return by_filter, by_group


def check_name(s: Session, table, name: str, own_id: Optional[int] = None) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if len(name) > 80:
        raise HTTPException(400, "Name is too long (max 80 characters)")
    for row in s.exec(select(table)).all():
        if row.id != own_id and row.name.lower() == name.lower():
            raise HTTPException(409, f"'{name}' already exists")
    return name


def check_rule(body: FilterIn) -> None:
    try:
        rules.validate_rule({"pattern": body.pattern, "stage": body.stage, "action": body.action, "validator": body.validator or None})
    except ValueError as e:
        raise HTTPException(400, str(e))


def filter_out(f: ContentFilter, group_ids: list, used: list) -> dict:
    return {**f.model_dump(), "group_ids": sorted(group_ids), "used_by": used}


@router.post("/content-filters/presets/install")
def install_presets(s: Session = Depends(get_session)):
    """Add the ready-made national-ID and GDPR filters and groups that are missing. Existing rows are never changed."""
    from ..db import install_presets as install

    res = install(s)
    library.invalidate()
    return res


@router.get("/content-filters")
def list_library(s: Session = Depends(get_session)):
    uf, ug = usage(s)
    links = s.exec(select(FilterGroupMember)).all()
    groups_of: dict = {}
    members_of: dict = {}
    for l in links:
        groups_of.setdefault(l.filter_id, []).append(l.group_id)
        members_of.setdefault(l.group_id, []).append(l.filter_id)
    return {
        "validators": [{"name": k, "label": v[1]} for k, v in checks.VALIDATORS.items()],
        "filters": [filter_out(f, groups_of.get(f.id, []), uf.get(f.id, [])) for f in s.exec(select(ContentFilter).order_by(ContentFilter.name)).all()],
        "groups": [{**g.model_dump(), "filter_ids": sorted(members_of.get(g.id, [])), "used_by": ug.get(g.id, [])}
                   for g in s.exec(select(FilterGroup).order_by(FilterGroup.name)).all()],
    }


def set_filter_groups(s: Session, fid: int, group_ids: list) -> None:
    for gid in group_ids:
        if not s.get(FilterGroup, gid):
            raise HTTPException(400, f"Unknown group id {gid}")
    for l in s.exec(select(FilterGroupMember).where(FilterGroupMember.filter_id == fid)).all():
        s.delete(l)
    s.flush()
    for gid in set(group_ids):
        s.add(FilterGroupMember(group_id=gid, filter_id=fid))


@router.post("/content-filters")
def create_filter(body: FilterIn, s: Session = Depends(get_session)):
    check_rule(body)
    f = ContentFilter(name=check_name(s, ContentFilter, body.name), description=body.description.strip(), pattern=body.pattern,
                      stage=body.stage, action=body.action, enabled=body.enabled, validator=body.validator or None)
    s.add(f)
    s.flush()
    set_filter_groups(s, f.id, body.group_ids)
    s.commit()
    s.refresh(f)
    library.invalidate()
    return filter_out(f, body.group_ids, [])


@router.put("/content-filters/{fid}")
def update_filter(fid: int, body: FilterIn, s: Session = Depends(get_session)):
    f = s.get(ContentFilter, fid)
    if not f:
        raise HTTPException(404)
    check_rule(body)
    f.name = check_name(s, ContentFilter, body.name, fid)
    f.description, f.pattern, f.stage, f.action, f.enabled = body.description.strip(), body.pattern, body.stage, body.action, body.enabled
    f.validator = body.validator or None
    s.add(f)
    set_filter_groups(s, fid, body.group_ids)
    s.commit()
    s.refresh(f)
    library.invalidate()
    return filter_out(f, body.group_ids, usage(s)[0].get(fid, []))


@router.delete("/content-filters/{fid}")
def delete_filter(fid: int, force: bool = False, s: Session = Depends(get_session)):
    f = s.get(ContentFilter, fid)
    if not f:
        raise HTTPException(404)
    used = usage(s)[0].get(fid, [])
    if used and not force:
        raise HTTPException(409, {"message": "This filter is used by gateway models", "used_by": used})
    for l in s.exec(select(FilterGroupMember).where(FilterGroupMember.filter_id == fid)).all():
        s.delete(l)
    s.delete(f)
    s.commit()
    library.invalidate()
    return {"ok": True}


def set_group_filters(s: Session, gid: int, filter_ids: list) -> None:
    for fid in filter_ids:
        if not s.get(ContentFilter, fid):
            raise HTTPException(400, f"Unknown filter id {fid}")
    for l in s.exec(select(FilterGroupMember).where(FilterGroupMember.group_id == gid)).all():
        s.delete(l)
    s.flush()
    for fid in set(filter_ids):
        s.add(FilterGroupMember(group_id=gid, filter_id=fid))


@router.post("/content-filter-groups")
def create_group(body: GroupIn, s: Session = Depends(get_session)):
    g = FilterGroup(name=check_name(s, FilterGroup, body.name), description=body.description.strip())
    s.add(g)
    s.flush()
    set_group_filters(s, g.id, body.filter_ids)
    s.commit()
    s.refresh(g)
    library.invalidate()
    return {**g.model_dump(), "filter_ids": sorted(set(body.filter_ids)), "used_by": []}


@router.put("/content-filter-groups/{gid}")
def update_group(gid: int, body: GroupIn, s: Session = Depends(get_session)):
    g = s.get(FilterGroup, gid)
    if not g:
        raise HTTPException(404)
    g.name, g.description = check_name(s, FilterGroup, body.name, gid), body.description.strip()
    s.add(g)
    set_group_filters(s, gid, body.filter_ids)
    s.commit()
    s.refresh(g)
    library.invalidate()
    return {**g.model_dump(), "filter_ids": sorted(set(body.filter_ids)), "used_by": usage(s)[1].get(gid, [])}


@router.delete("/content-filter-groups/{gid}")
def delete_group(gid: int, force: bool = False, s: Session = Depends(get_session)):
    g = s.get(FilterGroup, gid)
    if not g:
        raise HTTPException(404)
    used = usage(s)[1].get(gid, [])
    if used and not force:
        raise HTTPException(409, {"message": "This group is used by gateway models", "used_by": used})
    for l in s.exec(select(FilterGroupMember).where(FilterGroupMember.group_id == gid)).all():
        s.delete(l)
    s.delete(g)
    s.commit()
    library.invalidate()
    return {"ok": True}


@router.post("/content-filters/test")
def test_pattern(body: TestIn):
    rule = {"pattern": body.pattern, "validator": body.validator or None}
    try:
        rules.validate_rule(rule)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ms = rules.find(rule, body.text[:20000])
    return {"count": len(ms), "matches": [{"start": m.start(), "end": m.end(), "text": m.group(0)[:80]} for m in ms[:50]]}
