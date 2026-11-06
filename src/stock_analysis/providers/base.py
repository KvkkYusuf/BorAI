"""Base interface, data models, and exceptions for market data providers."""

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataStatus(StrEnum):
    """Lifecycle and verification state of a market data record."""

    PROVISIONAL = "provisional"  # Initial raw feed bar before reconciliation
    VERIFIED = "verified"  # Validated against integrity rules and sanity bounds
    CORRECTED = "corrected"  # Revised bar following post-session reconciliation
    FLAGGED = "flagged"  # Flagged for anomaly (e.g., volume spike, extreme volatility)


class MarketBarRecord(BaseModel):
    """Standardized market data record for daily EOD bars across all providers."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1, max_length=10, description="BIST ticker symbol (e.g., THYAO)")
    exchange: str = Field(default="BIST", min_length=1, max_length=10, description="Exchange code")
    trade_date: date = Field(description="Trading session date")
    timezone: str = Field(default="Europe/Istanbul", description="Exchange local IANA timezone")
    session_time_utc: datetime | None = Field(
        default=None,
        description="Session close timestamp in UTC",
    )
    currency: str = Field(default="TRY", min_length=3, max_length=3, description="ISO currency")
    data_status: DataStatus = Field(
        default=DataStatus.VERIFIED,
        description="Current data verification / lifecycle status",
    )

    # Raw prices (TRY)
    raw_open: Decimal = Field(gt=Decimal("0"))
    raw_high: Decimal = Field(gt=Decimal("0"))
    raw_low: Decimal = Field(gt=Decimal("0"))
    raw_close: Decimal = Field(gt=Decimal("0"))

    # Adjusted prices (TRY)
    adj_open: Decimal = Field(gt=Decimal("0"))
    adj_high: Decimal = Field(gt=Decimal("0"))
    adj_low: Decimal = Field(gt=Decimal("0"))
    adj_close: Decimal = Field(gt=Decimal("0"))

    # Volume and turnover
    volume: int = Field(ge=0, description="Trading volume in shares/lots")
    turnover_try: Decimal = Field(ge=Decimal("0"), description="Total turnover amount in TRY")

    # Corporate actions
    split_factor: Decimal = Field(default=Decimal("1.0"), gt=Decimal("0"))
    dividend_try: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"))

    # Provenance and metadata
    source_name: str = Field(description="Identifier of the data provider source")
    is_verified: bool = Field(default=True, description="Whether data passed validation rules")
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when record was ingested",
    )

    @model_validator(mode="after")
    def validate_record_integrity(self) -> "MarketBarRecord":
        """Validate logical OHLC boundaries, timezone validity, and UTC timestamps."""
        # 1. Validate timezone
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError(f"Invalid timezone '{self.timezone}': {exc}") from exc

        # 2. Validate session_time_utc if provided
        if self.session_time_utc is not None and self.session_time_utc.tzinfo is None:
            raise ValueError("session_time_utc must be timezone-aware (UTC)")

        # 3. Ingested at must be timezone-aware
        if self.ingested_at.tzinfo is None:
            raise ValueError("ingested_at must be timezone-aware (UTC)")

        # 4. Currency and exchange conventions
        if self.exchange.upper() != "BIST":
            raise ValueError(f"exchange must be 'BIST', got '{self.exchange}'")
        if self.currency.upper() != "TRY":
            raise ValueError(f"currency must be 'TRY', got '{self.currency}'")

        # 5. OHLC relationships
        if self.raw_high < self.raw_low:
            raise ValueError(
                f"raw_high ({self.raw_high}) cannot be less than raw_low ({self.raw_low})"
            )
        if self.raw_high < self.raw_open or self.raw_high < self.raw_close:
            raise ValueError("raw_high must be greater than or equal to raw_open and raw_close")
        if self.raw_low > self.raw_open or self.raw_low > self.raw_close:
            raise ValueError("raw_low must be less than or equal to raw_open and raw_close")

        if self.adj_high < self.adj_low:
            raise ValueError(
                f"adj_high ({self.adj_high}) cannot be less than adj_low ({self.adj_low})"
            )
        if self.adj_high < self.adj_open or self.adj_high < self.adj_close:
            raise ValueError("adj_high must be greater than or equal to adj_open and adj_close")
        if self.adj_low > self.adj_open or self.adj_low > self.adj_close:
            raise ValueError("adj_low must be less than or equal to adj_open and adj_close")

        return self


class ProviderHealth(BaseModel):
    """Health check and quota status for a market data provider."""

    model_config = ConfigDict(frozen=True)

    is_healthy: bool = Field(description="Whether provider is operational and reachable")
    provider_name: str = Field(description="Unique provider identifier")
    is_licensed: bool = Field(description="True if provider is an official licensed data feed")
    latency_ms: float | None = Field(default=None, description="Round-trip latency in ms")
    quota_remaining: int | None = Field(default=None, description="Remaining API calls today")
    quota_limit: int | None = Field(default=None, description="Total API call allowance today")
    message: str = Field(default="OK", description="Status message or error detail")


class ProviderError(Exception):
    """Base exception for all market data provider failures."""


class ProviderUnavailableError(ProviderError):
    """Raised when data provider endpoint is unreachable or down."""


class ProviderTimeoutError(ProviderUnavailableError):
    """Raised when data provider request times out due to network latency or backend delay."""


class ProviderQuotaExceededError(ProviderError):
    """Raised when data provider rate limit or quota is exhausted."""

    def __init__(
        self,
        message: str,
        quota_limit: int | None = None,
        retry_after_sec: float | None = None,
    ) -> None:
        super().__init__(message)
        self.quota_limit = quota_limit
        self.retry_after_sec = retry_after_sec


class ProviderDataFormatError(ProviderError):
    """Raised when provider returns corrupt or unexpected data format."""


class ProviderSchemaError(ProviderDataFormatError):
    """Raised when provider API payload violates expected schema or missing fields."""

    def __init__(self, message: str, missing_field: str | None = None) -> None:
        super().__init__(message)
        self.missing_field = missing_field


class BaseMarketDataProvider(ABC):
    """Abstract adapter interface for market data feeds.

    All concrete market data integrations (synthetic generator, licensed EOD
    APIs, Kaggle datasets) must implement this interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique provider name."""

    @property
    @abstractmethod
    def is_licensed(self) -> bool:
        """Return True if this provider is an authorized licensed feed."""

    @abstractmethod
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        """Fetch daily EOD bars for a symbol in the specified date range.

        Args:
            symbol: BIST ticker symbol (e.g. THYAO)
            start_date: Inclusive start date
            end_date: Inclusive end date

        Returns:
            List of validated MarketBarRecord instances sorted ascending by trade_date.
        """

    @abstractmethod
    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        """Fetch the latest available daily EOD bar for a symbol."""

    @abstractmethod
    def get_supported_symbols(self) -> list[str]:
        """Return list of supported/tracked ticker symbols."""

    @abstractmethod
    def health_check(self) -> ProviderHealth:
        """Perform a liveness check and report provider health and quota status."""
