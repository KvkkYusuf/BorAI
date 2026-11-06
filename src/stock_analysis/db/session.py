"""Database engine and session management for SQLAlchemy 2.0."""

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from stock_analysis.config import get_settings
from stock_analysis.db.slow_query import register_slow_query_logger

_engine: Engine | None = None
_session_factory: sessionmaker[DBSession] | None = None


def get_engine(db_url: str | None = None, **kwargs: Any) -> Engine:
    """Return singleton SQLAlchemy engine or create one for given URL."""
    global _engine
    settings = get_settings()
    if db_url is not None:
        connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}
        engine = create_engine(db_url, connect_args=connect_args, **kwargs)
        if settings.slow_query_logging_enabled:
            register_slow_query_logger(engine, threshold_ms=settings.slow_query_threshold_ms)
        return engine
    if _engine is None:
        connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}
        _engine = create_engine(settings.database_url, connect_args=connect_args, **kwargs)
        if settings.slow_query_logging_enabled:
            register_slow_query_logger(_engine, threshold_ms=settings.slow_query_threshold_ms)
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[DBSession]:
    """Return sessionmaker bound to engine."""
    global _session_factory
    if engine is not None:
        return sessionmaker(autocommit=False, autoflush=False, bind=engine)
    if _session_factory is None:
        _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _session_factory


def get_db() -> Generator[DBSession, None, None]:
    """FastAPI dependency yielding a transactional DB session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
