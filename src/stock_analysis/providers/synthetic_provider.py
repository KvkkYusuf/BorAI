"""Synthetic market data provider adapter for local development and tests."""

from datetime import UTC, date, datetime

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    DataStatus,
    MarketBarRecord,
    ProviderHealth,
)
from stock_analysis.synthetic import (
    BIST_SAMPLE_SYMBOLS,
    generate_synthetic_ohlcv,
)


class SyntheticMarketDataProvider(BaseMarketDataProvider):
    """Market data provider backed by the deterministic synthetic generator.

    Used strictly for local development and unit/integration tests without
    relying on live or licensed market data feeds.
    """

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._name = "synthetic_generator"

    @property
    def name(self) -> str:
        """Provider identifier."""
        return self._name

    @property
    def is_licensed(self) -> bool:
        """Synthetic data is not a licensed official market feed."""
        return False

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        """Generate and return synthetic daily bars converted to MarketBarRecord."""
        upper_symbol = symbol.strip().upper()
        synthetic_bars = generate_synthetic_ohlcv(
            symbol=upper_symbol,
            start_date=start_date,
            end_date=end_date,
            seed=self._seed + abs(hash(upper_symbol)) % 10000,
        )

        return [
            MarketBarRecord(
                symbol=bar.symbol,
                exchange="BIST",
                trade_date=bar.trade_date,
                timezone="Europe/Istanbul",
                session_time_utc=datetime(
                    bar.trade_date.year,
                    bar.trade_date.month,
                    bar.trade_date.day,
                    15,
                    10,
                    0,
                    tzinfo=UTC,
                ),
                currency="TRY",
                data_status=DataStatus.VERIFIED,
                raw_open=bar.raw_open,
                raw_high=bar.raw_high,
                raw_low=bar.raw_low,
                raw_close=bar.raw_close,
                adj_open=bar.adj_open,
                adj_high=bar.adj_high,
                adj_low=bar.adj_low,
                adj_close=bar.adj_close,
                volume=bar.volume,
                turnover_try=bar.turnover_try,
                split_factor=bar.split_factor,
                dividend_try=bar.dividend_try,
                source_name=self.name,
                is_verified=bar.is_verified,
            )
            for bar in synthetic_bars
        ]

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        """Fetch the single latest synthetic bar for the symbol as of today."""
        today = date.today()
        # Look back up to 7 days to cover weekends and holidays
        start = today.fromordinal(today.toordinal() - 7)
        bars = self.fetch_daily_bars(symbol=symbol, start_date=start, end_date=today)
        return bars[-1] if bars else None

    def get_supported_symbols(self) -> list[str]:
        """Return default sample BIST symbols supported by the synthetic provider."""
        return list(BIST_SAMPLE_SYMBOLS)

    def health_check(self) -> ProviderHealth:
        """Always healthy and operational with unlimited local quota."""
        return ProviderHealth(
            is_healthy=True,
            provider_name=self.name,
            is_licensed=self.is_licensed,
            latency_ms=0.1,
            quota_remaining=None,
            quota_limit=None,
            message="Synthetic generator operational (local test feed)",
        )
