from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import config
from . import models  # noqa: F401  (register tables)

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False}
                       if config.DATABASE_URL.startswith("sqlite") else {})

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def session():
    with Session(engine, expire_on_commit=False) as s:
        yield s
