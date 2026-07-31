"""SQLAlchemy engine and request-scoped session management."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from campaign_manager.config import Settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def configure_database(url: str | None = None) -> Engine:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    resolved_url = url or Settings.from_environment().database_url
    _engine = create_engine(resolved_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        return configure_database()
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    return _session_factory


def database_session() -> Generator[Session, None, None]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
