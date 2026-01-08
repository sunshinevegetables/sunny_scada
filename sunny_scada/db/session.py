from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


@dataclass(frozen=True)
class DBRuntime:
    engine: Engine
    SessionLocal: sessionmaker


def create_engine_and_sessionmaker(
    database_url: str,
    *,
    echo: bool = False,
    sqlite_check_same_thread: bool = False,
) -> DBRuntime:
    """Create SQLAlchemy engine + sessionmaker.

    Notes:
      - SQLite needs check_same_thread=False when used in FastAPI/threaded context.
      - Alembic migrations are added in Cycle 2; Cycle 1 can optionally use create_all.
    """
    is_sqlite = database_url.startswith("sqlite")

    connect_args: dict = {}
    if is_sqlite:
        # SQLite defaults to check_same_thread=True which breaks under FastAPI's threaded execution.
        # Also set a sane busy timeout for file locks.
        if not sqlite_check_same_thread:
            connect_args["check_same_thread"] = False
        connect_args.setdefault("timeout", 5)

    engine_kwargs = dict(
        echo=echo,
        future=True,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    if is_sqlite:
        # SQLite + QueuePool is a common source of "database is locked" and pool exhaustion in
        # threaded dev setups. NullPool is safer for SQLite file DBs.
        engine_kwargs["poolclass"] = NullPool

    engine = create_engine(database_url, **engine_kwargs)

    if is_sqlite:
        # Improve SQLite concurrent read/write behavior for dev usage.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
            except Exception:
                # If pragmas can't be applied (e.g., permissions), fail open for dev.
                pass
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return DBRuntime(engine=engine, SessionLocal=SessionLocal)
