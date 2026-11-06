"""Unit tests for data licensing policies, scraping prevention, and redistribution guards."""

from datetime import date
from decimal import Decimal

import pytest

from stock_analysis.pipeline.licensing import (
    DataUsagePolicy,
    DataUsageScope,
    RedistributionGuard,
    RedistributionPolicyViolationError,
    ScrapingGuard,
    UnauthorizedScrapingError,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord


def _make_bar(
    symbol: str = "THYAO",
    trade_date: date | None = None,
    source: str = "licensed_bist_eod",
) -> MarketBarRecord:
    return MarketBarRecord(
        symbol=symbol,
        trade_date=trade_date or date(2026, 10, 20),
        data_status=DataStatus.VERIFIED,
        raw_open=Decimal("280.0"),
        raw_high=Decimal("285.5"),
        raw_low=Decimal("278.0"),
        raw_close=Decimal("284.0"),
        adj_open=Decimal("280.0"),
        adj_high=Decimal("285.5"),
        adj_low=Decimal("278.0"),
        adj_close=Decimal("284.0"),
        volume=12_500_000,
        turnover_try=Decimal("3500000000.0"),
        currency="TRY",
        source_name=source,
    )


class TestScrapingGuard:
    """Tests for blocking web scraping and unauthorized crawling."""

    @pytest.mark.parametrize(
        "banned_channel",
        [
            "web_scraping",
            "html_parser",
            "dom_crawler",
            "headless_browser",
            "unauthorized_scraper",
        ],
    )
    def test_banned_channels_raise_error(self, banned_channel: str) -> None:
        with pytest.raises(UnauthorizedScrapingError, match="strictly prohibited"):
            ScrapingGuard.validate_source_channel(banned_channel, is_official_api=False)

    def test_unofficial_api_channel_raises_error(self) -> None:
        with pytest.raises(UnauthorizedScrapingError, match="not an authorized official API"):
            ScrapingGuard.validate_source_channel("unregistered_feed", is_official_api=False)

    def test_official_api_channel_passes(self) -> None:
        # Should not raise
        ScrapingGuard.validate_source_channel("licensed_bist_feed", is_official_api=True)

    def test_synthetic_channel_passes(self) -> None:
        # Synthetic generator is allowed in local dev/testing
        ScrapingGuard.validate_source_channel("synthetic", is_official_api=False)

    def test_validate_endpoint_url_valid(self) -> None:
        ScrapingGuard.validate_endpoint_url("https://api.marketdata.com/v1/eod")

    def test_validate_endpoint_url_invalid_scheme(self) -> None:
        with pytest.raises(UnauthorizedScrapingError, match="Invalid endpoint scheme"):
            ScrapingGuard.validate_endpoint_url("ftp://marketdata.com/feed")

    def test_validate_endpoint_url_rejects_html_page(self) -> None:
        with pytest.raises(UnauthorizedScrapingError, match="static or rendered web page"):
            ScrapingGuard.validate_endpoint_url("https://example.com/stocks/thyao.html")

    @pytest.mark.parametrize(
        "banned_agent",
        [
            "Mozilla/5.0 Scrapy/2.11.0 (+https://scrapy.org)",
            "Selenium/4.15 (python)",
            "Playwright-Crawler",
            "Python-BeautifulSoup/4.12",
            "Puppeteer-Extra",
            "curl/8.4.0",
        ],
    )
    def test_banned_user_agents_raise_error(self, banned_agent: str) -> None:
        with pytest.raises(UnauthorizedScrapingError, match="Forbidden scraping User-Agent"):
            ScrapingGuard.validate_client_headers({"User-Agent": banned_agent})

    def test_legitimate_client_header_passes(self) -> None:
        ScrapingGuard.validate_client_headers({"User-Agent": "BorAI-Client/1.0 (Borsa Analiz)"})


class TestRedistributionGuard:
    """Tests for preventing unauthorized bulk export and raw redistribution."""

    def test_direct_redistribution_blocked(self) -> None:
        guard = RedistributionGuard()
        with pytest.raises(RedistributionPolicyViolationError, match="Direct redistribution"):
            guard.validate_outbound_request(
                scope=DataUsageScope.REDISTRIBUTION,
                is_raw=True,
                record_count=10,
            )

    def test_raw_bulk_export_blocked(self) -> None:
        guard = RedistributionGuard()
        with pytest.raises(RedistributionPolicyViolationError, match="Bulk export of raw licensed"):
            guard.validate_outbound_request(
                scope=DataUsageScope.RAW_EXPORT,
                is_raw=True,
                record_count=10,
            )

    def test_excessive_display_bars_blocked(self) -> None:
        guard = RedistributionGuard(policy=DataUsagePolicy(max_display_bars=500))
        with pytest.raises(
            RedistributionPolicyViolationError,
            match="exceeds maximum display limit",
        ):
            guard.validate_outbound_request(
                scope=DataUsageScope.DISPLAY_ONLY,
                is_raw=True,
                record_count=1000,
            )

    def test_allowed_display_and_derived_requests(self) -> None:
        guard = RedistributionGuard(policy=DataUsagePolicy(max_display_bars=500))
        # Within limit display passes
        guard.validate_outbound_request(
            scope=DataUsageScope.DISPLAY_ONLY,
            is_raw=True,
            record_count=200,
        )
        # Derived metrics pass even with larger scope
        guard.validate_outbound_request(
            scope=DataUsageScope.DERIVED_METRIC,
            is_raw=False,
            record_count=500,
        )

    def test_sanitize_for_display(self) -> None:
        guard = RedistributionGuard()
        bar = _make_bar()
        sanitized = guard.sanitize_for_display(bar)

        assert sanitized["symbol"] == "THYAO"
        assert sanitized["trade_date"] == "2026-10-20"
        assert sanitized["close"] == 284.0
        assert sanitized["data_status"] == "verified"
        assert "attribution" in sanitized
        assert "Lisanslı Veri Sağlayıcı (EOD)" in sanitized["attribution"]["source"]
        assert "Yatırım tavsiyesi niteliği taşımaz" in sanitized["attribution"]["disclaimer"]

    def test_sanitize_for_display_without_attribution(self) -> None:
        policy = DataUsagePolicy(require_attribution=False)
        guard = RedistributionGuard(policy=policy)
        bar = _make_bar()
        sanitized = guard.sanitize_for_display(bar)

        assert "attribution" not in sanitized
