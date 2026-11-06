"""Data licensing and terms of service enforcement guards.

Prevents unauthorized web scraping, non-compliant data ingestion, and illegal
bulk redistribution or raw export of licensed market data in accordance with
provider terms and capital market compliance rules (COMPLIANCE_TR, FAZ-05).
"""

from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.providers.base import MarketBarRecord


class LicensingPolicyError(Exception):
    """Base exception for data licensing policy violations."""


class UnauthorizedScrapingError(LicensingPolicyError):
    """Raised when an unauthorized web scraping or crawling attempt is detected."""


class RedistributionPolicyViolationError(LicensingPolicyError):
    """Raised when unauthorized bulk export or direct raw redistribution is attempted."""


class DataUsageScope(StrEnum):
    """Allowed usage scopes for market data."""

    INTERNAL_ANALYSIS = "internal_analysis"
    DISPLAY_ONLY = "display_only"
    DERIVED_METRIC = "derived_metric"
    RAW_EXPORT = "raw_export"
    REDISTRIBUTION = "redistribution"


class DataUsagePolicy(BaseModel):
    """Configuration governing market data usage, redistribution limits, and scraping rules."""

    model_config = ConfigDict(frozen=True)

    allow_web_scraping: bool = Field(
        default=False,
        description="Zero-tolerance: unauthorized scraping of external websites is forbidden.",
    )
    allow_raw_redistribution: bool = Field(
        default=False,
        description="Redistribution of raw licensed market data is strictly forbidden.",
    )
    allow_bulk_raw_export: bool = Field(
        default=False,
        description="Bulk downloading of raw bars is restricted to prevent data leakage.",
    )
    require_attribution: bool = Field(
        default=True,
        description="Mandatory citation of provider source and timestamp delay status.",
    )
    max_display_bars: int = Field(
        default=1500,
        ge=1,
        description="Maximum number of bars permissible in a single client display request.",
    )


class ScrapingGuard:
    """Detects and blocks unauthorized web scraping sources and ingestion patterns."""

    DISALLOWED_CHANNELS: frozenset[str] = frozenset(
        {
            "web_scraping",
            "html_parser",
            "dom_crawler",
            "headless_browser",
            "unauthorized_scraper",
            "unofficial_crawler",
        }
    )

    DISALLOWED_USER_AGENTS: frozenset[str] = frozenset(
        {
            "scrapy",
            "selenium",
            "playwright",
            "beautifulsoup",
            "puppeteer",
            "curl",
        }
    )

    @classmethod
    def validate_source_channel(
        cls,
        source_channel: str,
        *,
        is_official_api: bool,
    ) -> None:
        """Validate ingestion source channel against scraping restrictions.

        Raises:
            UnauthorizedScrapingError: If source indicates scraping or lacks official authorization.
        """
        normalized_channel = source_channel.strip().lower()
        if normalized_channel in cls.DISALLOWED_CHANNELS:
            raise UnauthorizedScrapingError(
                f"Unauthorized data source channel '{source_channel}'. "
                "Web scraping is strictly prohibited under licensing policy."
            )

        if not is_official_api and normalized_channel != "synthetic":
            raise UnauthorizedScrapingError(
                f"Source channel '{source_channel}' is not an authorized official API feed "
                "or approved synthetic generator."
            )

    @classmethod
    def validate_endpoint_url(cls, endpoint_url: str) -> None:
        """Ensure endpoint is a recognized API schema, not an arbitrary web page scrape."""
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in ("https", "http"):
            raise UnauthorizedScrapingError(
                f"Invalid endpoint scheme '{parsed.scheme}'. Must be HTTP/HTTPS API."
            )

        path_lower = parsed.path.lower()
        if path_lower.endswith((".html", ".htm", ".aspx", ".php")) and "api" not in path_lower:
            raise UnauthorizedScrapingError(
                f"Endpoint '{endpoint_url}' appears to target a static or rendered web page. "
                "Scraping web pages is prohibited; use official JSON/REST API endpoints."
            )

    @classmethod
    def validate_client_headers(cls, headers: dict[str, str]) -> None:
        """Inspect client request headers to detect forbidden automated scrapers."""
        user_agent = headers.get("User-Agent", "").lower()
        for banned_agent in cls.DISALLOWED_USER_AGENTS:
            if banned_agent in user_agent:
                raise UnauthorizedScrapingError(
                    f"Forbidden scraping User-Agent detected: '{user_agent}'."
                )


class RedistributionGuard:
    """Enforces redistribution restrictions and sanitizes outbound market data."""

    def __init__(self, policy: DataUsagePolicy | None = None) -> None:
        self._policy = policy or DataUsagePolicy()

    @property
    def policy(self) -> DataUsagePolicy:
        return self._policy

    def validate_outbound_request(
        self,
        scope: DataUsageScope,
        *,
        is_raw: bool,
        record_count: int,
        consumer_role: str = "end_user",
    ) -> None:
        """Validate if an outbound data delivery conforms to licensing restrictions.

        Raises:
            RedistributionPolicyViolationError: If raw redistribution or bulk export is attempted.
        """
        if scope == DataUsageScope.REDISTRIBUTION:
            if not self._policy.allow_raw_redistribution:
                raise RedistributionPolicyViolationError(
                    "Direct redistribution of licensed market data to external parties "
                    "is strictly prohibited by licensing agreements."
                )

        if scope == DataUsageScope.RAW_EXPORT:
            if is_raw and not self._policy.allow_bulk_raw_export:
                raise RedistributionPolicyViolationError(
                    "Bulk export of raw licensed market data is prohibited. "
                    "Only derived metrics, charts, and summaries may be consumed."
                )

        if is_raw and record_count > self._policy.max_display_bars:
            raise RedistributionPolicyViolationError(
                f"Requested {record_count} bars exceeds maximum display limit "
                f"({self._policy.max_display_bars} bars). Bulk raw data harvesting is blocked."
            )

    def sanitize_for_display(
        self,
        record: MarketBarRecord,
        *,
        include_internal_source: bool = False,
    ) -> dict[str, Any]:
        """Convert a MarketBarRecord into a safe presentation structure.

        Strips internal system fields and ensures compliance with licensing attribution.
        """
        data: dict[str, Any] = {
            "symbol": record.symbol,
            "trade_date": record.trade_date.isoformat(),
            "open": float(record.adj_open),
            "high": float(record.adj_high),
            "low": float(record.adj_low),
            "close": float(record.adj_close),
            "volume": record.volume,
            "currency": record.currency,
            "data_status": record.data_status.value,
        }

        if self._policy.require_attribution:
            source_label = (
                record.source_name if include_internal_source else "Lisanslı Veri Sağlayıcı (EOD)"
            )
            data["attribution"] = {
                "source": source_label,
                "disclaimer": (
                    "Piyasa verileri gün sonu (EOD) kapanış kayıtlarıdır; gecikmelidir. "
                    "Yatırım tavsiyesi niteliği taşımaz."
                ),
            }

        return data
