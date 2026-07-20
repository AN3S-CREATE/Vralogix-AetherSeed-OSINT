"""Database engine and session management.

A synchronous SQLAlchemy engine keeps the storage layer simple and robust; DB
operations are short and are called from the async pipeline via brief critical
sections. SQLite gets ``check_same_thread=False`` plus WAL for concurrent reads.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aetherseed.config import Settings, get_settings
from aetherseed.core.storage.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _make_engine(settings: Settings) -> Engine:
    url = settings.database_url
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Ensure parent directory exists for file-based SQLite.
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, echo=False, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: object, _rec: object) -> None:
            cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = _make_engine(settings or get_settings())
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(settings), expire_on_commit=False, future=True)
    return _Session


def init_db(settings: Settings | None = None) -> None:
    """Create all tables. Idempotent; safe for dev. Use Alembic for prod migrations."""
    Base.metadata.create_all(get_engine(settings))


def reset_engine() -> None:
    """Dispose and forget the engine/session factory (used by tests)."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional session scope: commit on success, rollback on error.

    Examples
    --------
    >>> with session_scope() as s:  # doctest: +SKIP
    ...     s.add(record)
    """
    session = get_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session
