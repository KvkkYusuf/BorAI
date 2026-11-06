"""Tests for market data provider adapters, records, and factory."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stock_analysis.config import Settings
from stock_analysis.providers import (
    BaseMarketDataProvider,
    DataStatus,
    LicensedEODDataProvider,
    LicensedProviderAuthError,
    MarketBarRecord,
    ProviderUnavailableError,
    SyntheticMarketDataProvider,
    get_market_data_provider,
)


def test_market_bar_record_valid() -> None:
    """Verify that MarketBarRecord validates correctly for a valid bar."""
    bar = MarketBarRecord(
        symbol="THYAO",
        exchange="BIST",
        trade_date=date(2026, 1, 5),
        timezone="Europe/Istanbul",
        session_time_utc=datetime(2026, 1, 5, 15, 10, 0, tzinfo=UTC),
        currency="TRY",
        data_status=DataStatus.VERIFIED,
        raw_open=Decimal("100.0000"),
        raw_high=Decimal("105.0000"),
        raw_low=Decimal("98.0000"),
        raw_close=Decimal("102.0000"),
        adj_open=Decimal("100.0000"),
        adj_high=Decimal("105.0000"),
        adj_low=Decimal("98.0000"),
        adj_close=Decimal("102.0000"),
        volume=1_500_000,
        turnover_try=Decimal("153000000.00"),
        split_factor=Decimal("1.0"),
        dividend_try=Decimal("0.0"),
        source_name="test_source",
        is_verified=True,
    )
    assert bar.symbol == "THYAO"
    assert bar.exchange == "BIST"
    assert bar.currency == "TRY"
    assert bar.timezone == "Europe/Istanbul"
    assert bar.data_status == DataStatus.VERIFIED
    assert bar.session_time_utc == datetime(2026, 1, 5, 15, 10, 0, tzinfo=UTC)
    assert bar.raw_high >= bar.raw_open
    assert bar.raw_low <= bar.raw_close
    assert bar.is_verified is True


def test_market_bar_record_data_status_enums() -> None:
    """Verify all DataStatus enum values are valid."""
    for status in (
        DataStatus.PROVISIONAL,
        DataStatus.VERIFIED,
        DataStatus.CORRECTED,
        DataStatus.FLAGGED,
    ):
        bar = MarketBarRecord(
            symbol="THYAO",
            exchange="BIST",
            trade_date=date(2026, 1, 5),
            data_status=status,
            raw_open=Decimal("100.0"),
            raw_high=Decimal("105.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("102.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("102000.00"),
            source_name="test_source",
        )
        assert bar.data_status == status


def test_market_bar_record_timezone_and_timestamp_validation() -> None:
    """Verify timezone and timestamp integrity validations."""
    # Invalid timezone string
    with pytest.raises(ValueError, match="Invalid timezone"):
        MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            timezone="Invalid/Nonexistent_Zone",
            raw_open=Decimal("100.0"),
            raw_high=Decimal("105.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("102.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("102000.00"),
            source_name="test_source",
        )

    # Naive (timezone-unaware) session_time_utc
    with pytest.raises(ValueError, match="session_time_utc must be timezone-aware"):
        MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            session_time_utc=datetime(2026, 1, 5, 15, 10, 0),  # naive!
            raw_open=Decimal("100.0"),
            raw_high=Decimal("105.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("102.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("102000.00"),
            source_name="test_source",
        )


def test_market_bar_record_exchange_and_currency_validation() -> None:
    """Verify exchange and currency constraints for BIST records."""
    with pytest.raises(ValueError, match="exchange must be 'BIST'"):
        MarketBarRecord(
            symbol="THYAO",
            exchange="NASDAQ",
            trade_date=date(2026, 1, 5),
            raw_open=Decimal("100.0"),
            raw_high=Decimal("105.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("102.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("102000.00"),
            source_name="test_source",
        )

    with pytest.raises(ValueError, match="currency must be 'TRY'"):
        MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            currency="USD",
            raw_open=Decimal("100.0"),
            raw_high=Decimal("105.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("102.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("102000.00"),
            source_name="test_source",
        )


def test_market_bar_record_ohlc_invalid() -> None:
    """Verify that invalid OHLC relationships raise ValueError."""
    # high < low
    with pytest.raises(ValueError, match="raw_high .* cannot be less than raw_low"):
        MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            raw_open=Decimal("100.0"),
            raw_high=Decimal("90.0"),
            raw_low=Decimal("95.0"),
            raw_close=Decimal("92.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("102.0"),
            volume=1000,
            turnover_try=Decimal("92000.00"),
            source_name="test_source",
        )

    # raw_high < raw_close
    with pytest.raises(ValueError, match="raw_high must be greater than or equal"):
        MarketBarRecord(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            raw_open=Decimal("100.0"),
            raw_high=Decimal("101.0"),
            raw_low=Decimal("95.0"),
            raw_close=Decimal("104.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("105.0"),
            adj_low=Decimal("95.0"),
            adj_close=Decimal("104.0"),
            volume=1000,
            turnover_try=Decimal("104000.00"),
            source_name="test_source",
        )


def test_synthetic_provider_basic_contract() -> None:
    """Verify SyntheticMarketDataProvider implements BaseMarketDataProvider contract."""
    provider = SyntheticMarketDataProvider(seed=123)
    assert isinstance(provider, BaseMarketDataProvider)
    assert provider.name == "synthetic_generator"
    assert provider.is_licensed is False

    symbols = provider.get_supported_symbols()
    assert "THYAO" in symbols
    assert len(symbols) >= 5

    health = provider.health_check()
    assert health.is_healthy is True
    assert health.is_licensed is False
    assert health.provider_name == "synthetic_generator"


def test_synthetic_provider_fetch_bars() -> None:
    """Verify fetching daily bars from SyntheticMarketDataProvider."""
    provider = SyntheticMarketDataProvider()
    bars = provider.fetch_daily_bars(
        symbol="ASELS",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 15),
    )

    assert len(bars) > 0
    for bar in bars:
        assert isinstance(bar, MarketBarRecord)
        assert bar.symbol == "ASELS"
        assert bar.exchange == "BIST"
        assert bar.currency == "TRY"
        assert bar.raw_low <= bar.raw_open <= bar.raw_high
        assert bar.raw_low <= bar.raw_close <= bar.raw_high
        assert bar.volume >= 0
        assert bar.turnover_try >= Decimal("0")

    # Dates must be monotonically increasing
    dates = [b.trade_date for b in bars]
    assert dates == sorted(dates)


def test_synthetic_provider_fetch_latest_bar() -> None:
    """Verify fetch_latest_bar returns the most recent trading bar."""
    provider = SyntheticMarketDataProvider()
    latest_bar = provider.fetch_latest_bar(symbol="GARAN")
    assert latest_bar is not None
    assert latest_bar.symbol == "GARAN"
    assert latest_bar.trade_date <= date.today()


class DummyLicensedProvider(LicensedEODDataProvider):
    """Concrete dummy implementation of LicensedEODDataProvider for tests."""

    @property
    def name(self) -> str:
        return "dummy_licensed_feed"

    def get_daily_quota_status(self) -> tuple[int | None, int | None]:
        return (450, 500)

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        return []

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        return None

    def get_supported_symbols(self) -> list[str]:
        return ["THYAO"]


def test_licensed_provider_auth_enforcement() -> None:
    """Verify LicensedEODDataProvider rejects invalid or placeholder API keys."""
    with pytest.raises(LicensedProviderAuthError, match="Valid API key is required"):
        DummyLicensedProvider(api_key="", base_url="https://api.example.com")

    with pytest.raises(LicensedProviderAuthError, match="Valid API key is required"):
        DummyLicensedProvider(api_key="changeme", base_url="https://api.example.com")

    # Valid key should succeed and report health
    provider = DummyLicensedProvider(
        api_key="valid-secret-key-12345",
        base_url="https://api.example.com",
    )
    assert provider.is_licensed is True
    health = provider.health_check()
    assert health.is_healthy is True
    assert health.quota_remaining == 450
    assert health.quota_limit == 500


def test_provider_factory() -> None:
    """Verify provider factory returns appropriate provider according to settings."""
    settings = Settings(MARKET_DATA_PROVIDER="mock")
    provider = get_market_data_provider(settings=settings)
    assert isinstance(provider, SyntheticMarketDataProvider)

    # Explicit override to synthetic
    provider_syn = get_market_data_provider(provider_name="synthetic")
    assert isinstance(provider_syn, SyntheticMarketDataProvider)

    # Unknown or unconfigured licensed provider raises ProviderUnavailableError
    with pytest.raises(ProviderUnavailableError, match="not configured or licensed"):
        get_market_data_provider(provider_name="matriks_live")
