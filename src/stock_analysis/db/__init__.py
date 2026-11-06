"""Database models and schema export."""

from stock_analysis.db.base import Base, TimestampMixin
from stock_analysis.db.models import (
    AlertEvent,
    AlertRule,
    AuditEvent,
    CorporateAction,
    Forecast,
    Instrument,
    MarketBar,
    ModelVersion,
    PasskeyCredential,
    Session,
    User,
    Watchlist,
    WatchlistItem,
)
from stock_analysis.db.safe_query import (
    UnsafeQueryError,
    escape_like_pattern,
    safe_text,
    validate_executable,
)
from stock_analysis.db.session import get_db, get_engine, get_session_factory
from stock_analysis.db.slow_query import (
    SlowQueryEvent,
    SlowQueryTracker,
    get_slow_query_tracker,
    register_slow_query_logger,
)

__all__ = [
    "AlertEvent",
    "AlertRule",
    "AuditEvent",
    "Base",
    "CorporateAction",
    "Forecast",
    "Instrument",
    "MarketBar",
    "ModelVersion",
    "PasskeyCredential",
    "Session",
    "SlowQueryEvent",
    "SlowQueryTracker",
    "TimestampMixin",
    "UnsafeQueryError",
    "User",
    "Watchlist",
    "WatchlistItem",
    "escape_like_pattern",
    "get_db",
    "get_engine",
    "get_session_factory",
    "get_slow_query_tracker",
    "register_slow_query_logger",
    "safe_text",
    "validate_executable",
]
