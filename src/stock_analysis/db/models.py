"""SQLAlchemy 2.0 ORM models for Stock Analysis platform.

Covers all core business and operational tables:
- users
- sessions / identity mappings
- instruments
- market_bars (with composite indexes and data versioning)
- corporate_actions
- watchlists
- watchlist_items
- alert_rules
- alert_events
- model_versions
- forecasts
- audit_events
- user_preferences
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stock_analysis.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """User account entity."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mfa_recovery_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    sessions: Mapped[list[Session]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    watchlists: Mapped[list[Watchlist]] = relationship(
        "Watchlist", back_populates="user", cascade="all, delete-orphan"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        "AlertRule", back_populates="user", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship("AuditEvent", back_populates="user")
    preferences: Mapped[UserPreference | None] = relationship(
        "UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    passkeys: Mapped[list[PasskeyCredential]] = relationship(
        "PasskeyCredential", back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    """User active session and identity mapping record."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_token_hash: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="sessions")


class Instrument(Base, TimestampMixin):
    """Financial instrument / stock security."""

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    market: Mapped[str] = mapped_column(String(32), default="BIST", index=True, nullable=False)
    isin: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="TRY", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    market_bars: Mapped[list[MarketBar]] = relationship(
        "MarketBar", back_populates="instrument", cascade="all, delete-orphan"
    )
    corporate_actions: Mapped[list[CorporateAction]] = relationship(
        "CorporateAction", back_populates="instrument", cascade="all, delete-orphan"
    )
    watchlist_items: Mapped[list[WatchlistItem]] = relationship(
        "WatchlistItem", back_populates="instrument"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship("AlertRule", back_populates="instrument")
    forecasts: Mapped[list[Forecast]] = relationship("Forecast", back_populates="instrument")


class MarketBar(Base):
    """OHLCV market price record with UTC timestamps and data versioning."""

    __tablename__ = "market_bars"
    __table_args__ = (
        Index("ix_market_bars_inst_ts", "instrument_id", "timestamp"),
        Index("ix_market_bars_inst_adj_ts", "instrument_id", "is_adjusted", "timestamp"),
        UniqueConstraint(
            "instrument_id",
            "timestamp",
            "is_adjusted",
            "data_version",
            name="uq_market_bars_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    is_adjusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="market_bars")


class CorporateAction(Base):
    """Corporate actions affecting security pricing and splits."""

    __tablename__ = "corporate_actions"
    __table_args__ = (Index("ix_corp_actions_inst_date", "instrument_id", "ex_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)  # dividend, split, bonus
    ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="corporate_actions")


class Watchlist(Base, TimestampMixin):
    """User-curated list of watched instruments."""

    __tablename__ = "watchlists"
    __table_args__ = (Index("ix_watchlists_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    user: Mapped[User] = relationship("User", back_populates="watchlists")
    items: Mapped[list[WatchlistItem]] = relationship(
        "WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    """Individual item entry in a user watchlist."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id", name="uq_watchlist_instrument"),
        Index("ix_watchlist_items_inst", "instrument_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    watchlist: Mapped[Watchlist] = relationship("Watchlist", back_populates="items")
    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="watchlist_items")


class AlertRule(Base):
    """User-configured price or indicator threshold trigger."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_user_inst", "user_id", "instrument_id"),
        Index("ix_alert_rules_inst_active", "instrument_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    condition_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="alert_rules")
    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="alert_rules")
    events: Mapped[list[AlertEvent]] = relationship(
        "AlertEvent", back_populates="alert_rule", cascade="all, delete-orphan"
    )


class AlertEvent(Base):
    """Fired alert notification instance."""

    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_rule_triggered", "alert_rule_id", "triggered_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    alert_rule: Mapped[AlertRule] = relationship("AlertRule", back_populates="events")


class ModelVersion(Base):
    """Machine learning model artifact metadata and tracking."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version_tag: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    metrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    forecasts: Mapped[list[Forecast]] = relationship("Forecast", back_populates="model_version")


class Forecast(Base):
    """Inference/forecast output generated by a model version."""

    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_forecasts_inst_target", "instrument_id", "target_date"),
        Index("ix_forecasts_model_target", "model_version_id", "target_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    model_version: Mapped[ModelVersion] = relationship("ModelVersion", back_populates="forecasts")
    instrument: Mapped[Instrument] = relationship("Instrument", back_populates="forecasts")


class AuditEvent(Base):
    """Security and compliance audit trail log."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_user_action", "user_id", "action"),
        Index("ix_audit_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped[User | None] = relationship("User", back_populates="audit_events")


class UserPreference(Base, TimestampMixin):
    """User-specific settings and preferences."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Istanbul", nullable=False)
    theme: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="tr", nullable=False)
    alert_frequency: Mapped[str] = mapped_column(String(32), default="immediate", nullable=False)
    weekly_digest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="preferences")


class PasskeyCredential(Base, TimestampMixin):
    """FIDO2 / WebAuthn passkey credential entity."""

    __tablename__ = "passkey_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    device_name: Mapped[str] = mapped_column(
        String(128), default="Varsayılan Güvenlik Anahtarı", nullable=False
    )
    aaguid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transports: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="passkeys")
