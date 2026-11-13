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
