"""SQLAlchemy 2.0 engine/session factory.

SQLite connections get ``journal_mode=WAL`` and ``foreign_keys=ON`` on every connect.
Any other backend (e.g. PostgreSQL via ``DATABASE_URL``) is used as-is.
"""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base


def _is_sqlite_memory(database: str | None) -> bool:
    return database in (None, "", ":memory:") or (database or "").startswith("file::memory:")


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    finally:
        cursor.close()


def build_engine(database_url: str, *, echo: bool = False) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(url, echo=echo, pool_pre_ping=True)

    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if _is_sqlite_memory(url.database):
        # One shared connection so every session sees the same in-memory database.
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, echo=echo, **kwargs)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


_settings = get_settings()
engine: Engine = build_engine(_settings.database_url, echo=_settings.database_echo)
SessionLocal: sessionmaker[Session] = build_session_factory(engine)


def init_db(bind: Engine | None = None) -> None:
    # Import models so they register on Base.metadata before create_all.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=bind or engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
