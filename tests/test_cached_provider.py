"""Tests for cached market data provider, in-memory cache, and quota tracking."""

from datetime import date
from decimal import Decimal

import pytest

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    MarketBarRecord,
    ProviderHealth,
    ProviderQuotaExceededError,
)
from stock_analysis.providers.cached_provider import (
    CachedMarketDataProvider,
    MarketDataCache,
    ProviderQuotaTracker,
    generate_data_version,
)


def _make_sample_bar(trade_date: date, symbol: str = "THYAO") -> MarketBarRecord:
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        raw_open=Decimal("100.00"),
        raw_high=Decimal("105.00"),
        raw_low=Decimal("98.00"),
        raw_close=Decimal("102.00"),
        adj_open=Decimal("100.00"),
        adj_high=Decimal("105.00"),
        adj_low=Decimal("98.00"),
        adj_close=Decimal("102.00"),
        volume=10000,
        turnover_try=Decimal("1020000.00"),
        source_name="mock_feed",
    )


class SpyMarketDataProvider(BaseMarketDataProvider):
    """Spy provider recording invocations for caching tests."""

    def __init__(self) -> None:
        self.daily_fetch_calls = 0
        self.latest_fetch_calls = 0

    @property
    def name(self) -> str:
        return "spy_provider"

    @property
    def is_licensed(self) -> bool:
        return True

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        self.daily_fetch_calls += 1
        return [_make_sample_bar(start_date, symbol=symbol)]

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        self.latest_fetch_calls += 1
        return _make_sample_bar(date(2026, 1, 5), symbol=symbol)

    def get_supported_symbols(self) -> list[str]:
        return ["THYAO", "KCHOL"]

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            is_healthy=True,
            provider_name=self.name,
            is_licensed=True,
            message="Spy is healthy",
        )


class TestProviderQuotaTracker:
    def test_tracker_accounting_and_limits(self) -> None:
        tracker = ProviderQuotaTracker(daily_limit=2)
        assert tracker.quota_remaining == 2
        assert tracker.calls_made == 0

        tracker.record_call("THYAO")
        assert tracker.calls_made == 1
        assert tracker.quota_remaining == 1

        tracker.record_cache_hit("THYAO")
        assert tracker.cache_hits == 1

        tracker.record_call("KCHOL")
        assert tracker.calls_made == 2
        assert tracker.quota_remaining == 0

        # Next call must raise ProviderQuotaExceededError
        with pytest.raises(ProviderQuotaExceededError) as exc_info:
            tracker.record_call("THYAO")
        assert "quota limit (2) exhausted" in str(exc_info.value)
        assert exc_info.value.quota_limit == 2

    def test_tracker_stats_and_reset(self) -> None:
        tracker = ProviderQuotaTracker(daily_limit=10)
        tracker.record_call("THYAO")
        tracker.record_cache_hit("THYAO")

        stats = tracker.get_stats()
        assert stats.calls_made == 1
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1
        assert stats.hit_ratio == 0.5
        assert stats.calls_remaining == 9

        tracker.reset()
        assert tracker.calls_made == 0
        assert tracker.cache_hits == 0
        assert tracker.quota_remaining == 10


class TestMarketDataCache:
    def test_range_cache_lifecycle(self) -> None:
        cache = MarketDataCache()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        bar = _make_sample_bar(d1)

        assert cache.get_daily_bars("THYAO", d1, d2) is None
        cache.set_daily_bars("THYAO", d1, d2, [bar])

        cached = cache.get_daily_bars("thyao", d1, d2)
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].symbol == "THYAO"

        # Symbol eviction
        cache.clear(symbol="THYAO")
        assert cache.get_daily_bars("THYAO", d1, d2) is None

    def test_latest_bar_ttl(self) -> None:
        import time

        cache = MarketDataCache()
        bar = _make_sample_bar(date(2026, 1, 5))
        cache.set_latest_bar("THYAO", bar)

        # Within TTL
        assert cache.get_latest_bar("THYAO", max_age_seconds=60.0) is not None
        # Expired TTL
        cache._latest_cache["THYAO"] = (time.monotonic() - 100.0, bar)
        assert cache.get_latest_bar("THYAO", max_age_seconds=60.0) is None


class TestCachedMarketDataProvider:
    def test_caching_prevents_duplicate_network_calls(self) -> None:
        spy = SpyMarketDataProvider()
        cached_provider = CachedMarketDataProvider(inner_provider=spy, daily_quota_limit=10)

        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)

        # 1. First fetch: miss -> calls inner provider
        bars1 = cached_provider.fetch_daily_bars("THYAO", d1, d2)
        assert len(bars1) == 1
        assert spy.daily_fetch_calls == 1
        assert cached_provider.quota_tracker.calls_made == 1
        assert cached_provider.quota_tracker.cache_hits == 0

        # 2. Second fetch with identical parameters: hit -> does NOT call inner provider
        bars2 = cached_provider.fetch_daily_bars("THYAO", d1, d2)
        assert len(bars2) == 1
        assert spy.daily_fetch_calls == 1  # Unchanged!
        assert cached_provider.quota_tracker.calls_made == 1
        assert cached_provider.quota_tracker.cache_hits == 1

    def test_latest_bar_caching(self) -> None:
        spy = SpyMarketDataProvider()
        cached_provider = CachedMarketDataProvider(inner_provider=spy, latest_bar_ttl_seconds=300.0)

        bar1 = cached_provider.fetch_latest_bar("THYAO")
        assert bar1 is not None
        assert spy.latest_fetch_calls == 1

        bar2 = cached_provider.fetch_latest_bar("THYAO")
        assert bar2 is not None
        assert spy.latest_fetch_calls == 1  # Served from cache

    def test_cached_queries_allowed_even_when_quota_exhausted(self) -> None:
        spy = SpyMarketDataProvider()
        # Limit of 1 external call
        cached_provider = CachedMarketDataProvider(inner_provider=spy, daily_quota_limit=1)

        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)

        # First call succeeds and caches
        cached_provider.fetch_daily_bars("THYAO", d1, d2)
        assert cached_provider.quota_tracker.quota_remaining == 0

        # Second call for the SAME data succeeds via cache without raising quota error
        cached_bars = cached_provider.fetch_daily_bars("THYAO", d1, d2)
        assert len(cached_bars) == 1
        assert spy.daily_fetch_calls == 1

        # Third call for NEW data fails due to quota limit
        with pytest.raises(ProviderQuotaExceededError):
            cached_provider.fetch_daily_bars("KCHOL", d1, d2)

    def test_health_check_overlays_metrics(self) -> None:
        spy = SpyMarketDataProvider()
        cached_provider = CachedMarketDataProvider(inner_provider=spy, daily_quota_limit=5)

        d1 = date(2026, 1, 5)
        cached_provider.fetch_daily_bars("THYAO", d1, d1)
        cached_provider.fetch_daily_bars("THYAO", d1, d1)  # hit

        health = cached_provider.health_check()
        assert health.is_healthy is True
        assert health.provider_name == "cached_spy_provider"
        assert health.quota_remaining == 4
        assert health.quota_limit == 5
        assert "Cache: 1 hits, 1 misses" in health.message


class TestGenerateDataVersion:
    def test_deterministic_generation(self) -> None:
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        bar1 = _make_sample_bar(d1, "THYAO")
        bar2 = _make_sample_bar(d2, "THYAO")

        v1 = generate_data_version("THYAO", [bar1, bar2])
        v2 = generate_data_version("THYAO", [bar2, bar1])  # Order-independent (sorted by date)
        assert v1.startswith("dv_")
        assert v1 == v2

    def test_version_changes_with_price_adjustment(self) -> None:
        d1 = date(2026, 1, 5)
        bar_orig = _make_sample_bar(d1, "THYAO")
        bar_adj = bar_orig.model_copy(update={"raw_close": Decimal("108.50")})

        v_orig = generate_data_version("THYAO", [bar_orig])
        v_adj = generate_data_version("THYAO", [bar_adj])
        assert v_orig != v_adj

    def test_tag_alters_version(self) -> None:
        d1 = date(2026, 1, 5)
        bar = _make_sample_bar(d1, "THYAO")

        v_standard = generate_data_version("THYAO", [bar])
        v_tagged = generate_data_version("THYAO", [bar], tag="corporate_action_adjusted")
        assert v_standard != v_tagged


class TestVersionBoundMarketDataCache:
    def test_multi_version_coexistence(self) -> None:
        cache = MarketDataCache()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        bar_v1 = _make_sample_bar(d1, "THYAO")
        bar_v2 = bar_v1.model_copy(update={"raw_close": Decimal("120.00")})

        # Store two versions for the exact same symbol and date range
        v1 = cache.set_daily_bars("THYAO", d1, d2, [bar_v1], data_version="dv_initial")
        v2 = cache.set_daily_bars("THYAO", d1, d2, [bar_v2], data_version="dv_revised")
        assert v1 == "dv_initial"
        assert v2 == "dv_revised"

        # Explicit retrieval by version
        retrieved_v1 = cache.get_daily_bars("THYAO", d1, d2, data_version="dv_initial")
        retrieved_v2 = cache.get_daily_bars("THYAO", d1, d2, data_version="dv_revised")
        assert retrieved_v1 is not None and retrieved_v1[0].raw_close == Decimal("102.00")
        assert retrieved_v2 is not None and retrieved_v2[0].raw_close == Decimal("120.00")

        # Querying with unknown version returns None
        assert cache.get_daily_bars("THYAO", d1, d2, data_version="dv_nonexistent") is None

        # Omission of data_version returns the latest saved version (dv_revised)
        latest = cache.get_daily_bars("THYAO", d1, d2)
        assert latest is not None and latest[0].raw_close == Decimal("120.00")

        # Check cached versions listing
        versions = cache.get_cached_versions("THYAO", d1, d2)
        assert sorted(versions) == ["dv_initial", "dv_revised"]

    def test_eviction_by_specific_version(self) -> None:
        cache = MarketDataCache()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)
        bar = _make_sample_bar(d1, "THYAO")

        cache.set_daily_bars("THYAO", d1, d2, [bar], data_version="v1")
        cache.set_daily_bars("THYAO", d1, d2, [bar], data_version="v2")

        # Evict only v1
        cache.clear(symbol="THYAO", data_version="v1")
        assert cache.get_daily_bars("THYAO", d1, d2, data_version="v1") is None
        assert cache.get_daily_bars("THYAO", d1, d2, data_version="v2") is not None

    def test_provider_with_data_version_query(self) -> None:
        spy = SpyMarketDataProvider()
        cached_provider = CachedMarketDataProvider(inner_provider=spy, daily_quota_limit=10)

        d1 = date(2026, 1, 5)
        d2 = date(2026, 1, 6)

        # First fetch with version v1 triggers external call
        bars_v1 = cached_provider.fetch_daily_bars("THYAO", d1, d2, data_version="v1")
        assert len(bars_v1) == 1
        assert spy.daily_fetch_calls == 1

        # Second fetch with SAME version v1 is a cache hit (no new external call)
        bars_v1_repeat = cached_provider.fetch_daily_bars("THYAO", d1, d2, data_version="v1")
        assert len(bars_v1_repeat) == 1
        assert spy.daily_fetch_calls == 1

        # Third fetch asking for a DIFFERENT version (v2) triggers external fetch
        bars_v2 = cached_provider.fetch_daily_bars("THYAO", d1, d2, data_version="v2")
        assert len(bars_v2) == 1
        assert spy.daily_fetch_calls == 2
