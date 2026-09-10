from __future__ import annotations

import logging
from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import config
from . import models  # noqa: F401  (register tables)

log = logging.getLogger("sahara.db")

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False}
                       if config.DATABASE_URL.startswith("sqlite") else {})

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Tiny forward-only migration: create_all makes new tables but never alters existing
    ones, so a database from an earlier version loses a whole call to a missing column.
    Only additive; anything more needs a real migration tool."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or not (col.nullable or col.default is not None):
                    continue
                ddl = col.type.compile(engine.dialect)
                default = "''" if "CHAR" in ddl.upper() or "TEXT" in ddl.upper() else "NULL"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl} '
                                  f"DEFAULT {default}"))
                log.info("migrated: added %s.%s", table.name, col.name)


@contextmanager
def session():
    with Session(engine, expire_on_commit=False) as s:
        yield s
