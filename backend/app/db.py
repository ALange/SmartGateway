import os
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from . import config

os.makedirs(config.DATA_DIR, exist_ok=True)
engine = create_engine(
    f"sqlite:///{os.path.join(config.DATA_DIR, 'smartgateway.db')}",
    connect_args={"check_same_thread": False},
)


def _migrate() -> None:
    """Add columns that exist in the models but not yet in an older SQLite DB (additive only)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                typ = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {typ}{default}'))


def init_db() -> None:
    from . import crypto
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _migrate()
    crypto.encrypt_existing(engine)
    _migrate_legacy_keys()
    _migrate_cache_mode()
    _migrate_inline_filters()
    _seed_presets()


def _migrate_legacy_keys() -> None:
    """v1 stored one plaintext key on the gateway; turn it into a hashed ApiKey row so clients keep working."""
    from sqlmodel import select

    from .crypto import hash_key
    from .models import ApiKey, Gateway

    with Session(engine) as s:
        for gw in s.exec(select(Gateway)).all():
            if gw.api_key and not s.exec(select(ApiKey).where(ApiKey.gateway_id == gw.id)).first():
                s.add(ApiKey(gateway_id=gw.id, name="default (migrated)", key_hash=hash_key(gw.api_key),
                             key_prefix=gw.api_key[:6] + "…"))
                gw.api_key = None
                s.add(gw)
        s.commit()


@contextmanager
def session():
    with Session(engine) as s:
        yield s


def get_session():
    with Session(engine) as s:
        yield s


def _migrate_cache_mode() -> None:
    """v2 had one cache switch per gateway; caching is now configured per public model."""
    from sqlmodel import select

    from .models import Gateway, GatewayModel

    with Session(engine) as s:
        for gw in s.exec(select(Gateway)).all():
            if gw.cache_mode == "exact":
                for gm in s.exec(select(GatewayModel).where(GatewayModel.gateway_id == gw.id)).all():
                    gm.cache_enabled = True
                    s.add(gm)
                gw.cache_mode = "off"
                s.add(gw)
        s.commit()


def _migrate_inline_filters() -> None:
    """Content filters used to be typed into each gateway model; they now live in a library that models reference."""
    from sqlmodel import select

    from .models import ContentFilter, FilterGroup, FilterGroupMember, Gateway, GatewayModel

    with Session(engine) as s:
        todo = [gm for gm in s.exec(select(GatewayModel)).all() if "rules" in ((gm.guardrails or {}).get("filters") or {})]
        if not todo:
            return
        taken = {f.name.lower() for f in s.exec(select(ContentFilter)).all()}
        taken_groups = {g.name.lower() for g in s.exec(select(FilterGroup)).all()}
        shared: dict = {}  # identical rules are migrated once

        def unique(base: str, used: set) -> str:
            name, n = base, 2
            while name.lower() in used:
                name, n = f"{base} ({n})", n + 1
            used.add(name.lower())
            return name

        for gm in todo:
            cfg = dict(gm.guardrails)
            f = dict(cfg["filters"])
            rules = f.pop("rules") or []
            gids = list(f.get("group_ids") or [])
            if rules:
                gw = s.get(Gateway, gm.gateway_id)
                grp = FilterGroup(name=unique(f"Migrated: {gw.slug if gw else gm.gateway_id}/{gm.public_name}", taken_groups),
                                  description="Created from rules that were typed into this gateway model")
                s.add(grp)
                s.flush()
                for i, r in enumerate(rules, 1):
                    k = (r.get("pattern"), r.get("stage", "both"), r.get("action", "block"))
                    if k not in shared:
                        flt = ContentFilter(name=unique(r.get("name") or f"Migrated rule {i}", taken), pattern=k[0], stage=k[1], action=k[2])
                        s.add(flt)
                        s.flush()
                        shared[k] = flt.id
                    s.add(FilterGroupMember(group_id=grp.id, filter_id=shared[k]))
                gids.append(grp.id)
            f["group_ids"] = gids
            f.setdefault("filter_ids", [])
            cfg["filters"] = f
            gm.guardrails = cfg
            s.add(gm)
        s.commit()


def install_presets(s) -> dict:
    """Add the ready-made content filters and groups that are not there yet (matched by name). Never changes or removes anything."""
    from sqlmodel import select

    from .guardrails import presets
    from .models import ContentFilter, FilterGroup, FilterGroupMember

    filters = {f.name.lower(): f for f in s.exec(select(ContentFilter)).all()}
    groups = {g.name.lower(): g for g in s.exec(select(FilterGroup)).all()}
    added_f = added_g = 0
    new_groups = set()
    for name, desc in presets.GROUPS.items():
        if name.lower() not in groups:
            g = FilterGroup(name=name, description=desc)
            s.add(g)
            s.flush()
            groups[name.lower()] = g
            new_groups.add(name.lower())
            added_g += 1
    for p in presets.FILTERS:
        f = filters.get(p["name"].lower())
        is_new = f is None
        if is_new:
            f = ContentFilter(name=p["name"], description=p["description"], pattern=p["pattern"], stage=p["stage"], action=p["action"], validator=p["validator"])
            s.add(f)
            s.flush()
            filters[p["name"].lower()] = f
            added_f += 1
        for gname in p["groups"]:   # link only when one side was just created: a group and filter that both already existed are yours
            if is_new or gname.lower() in new_groups:
                s.add(FilterGroupMember(group_id=groups[gname.lower()].id, filter_id=f.id))
    s.commit()
    return {"added_filters": added_f, "added_groups": added_g}


def _seed_presets() -> None:
    """First start after the preset library shipped: install it once. A deleted preset is not brought back by a restart."""
    from sqlmodel import select

    from .models import Setting

    with Session(engine) as s:
        if s.get(Setting, "presets_seeded"):
            return
        install_presets(s)
        s.add(Setting(key="presets_seeded", value="1"))
        s.commit()
