"""Tests for cache hit ratio and prevention of redundant LLM calls.

Verifies:
1. Cache hit ratio calculation accuracy for market data provider and quota tracker.
2. SafeLLMExplainer prevention of repetitive LLM invocations for identical inputs.
3. Cache key stability against symbol case and whitespace variations.
4. Cache invalidation / miss upon indicator, forecast, or context changes.
5. Cache TTL expiration and safe re-fetching.
6. Multi-request workload simulation verifying exact hit ratio mathematics.
7. Resilience against caching unsafe or failed LLM responses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from stock_analysis.config import Settings
from stock_analysis.models.llm_explainer import (
    MockSafeLLMClient,
    SafeLLMExplainer,
)
from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    MarketBarRecord,
    ProviderHealth,
)
from stock_analysis.providers.cached_provider import (
    CachedMarketDataProvider,
    ProviderQuotaTracker,
)


def _make_bar(trade_date: date, symbol: str = "THYAO") -> MarketBarRecord:
    return MarketBarRecord(
        symbol=symbol.upper(),
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
    """Spy provider tracking total calls made."""

    def __init__(self) -> None:
        self.daily_fetch_calls = 0
        self.latest_fetch_calls = 0

    @property
    def name(self) -> str:
        return "spy_test_provider"

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
        return [_make_bar(start_date, symbol=symbol)]

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        self.latest_fetch_calls += 1
        return _make_bar(date(2026, 1, 5), symbol=symbol)

    def get_supported_symbols(self) -> list[str]:
        return ["THYAO", "KCHOL", "ASELS"]

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            is_healthy=True,
            provider_name=self.name,
            is_licensed=True,
            message="Spy provider healthy",
        )


class TestProviderCacheHitRatio:
    """Test suite for Market Data Cache hit ratio and quota tracking."""

    def test_quota_tracker_initial_hit_ratio_is_zero(self) -> None:
        tracker = ProviderQuotaTracker(daily_limit=100)
        stats = tracker.get_stats()
        assert stats.calls_made == 0
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0
        assert stats.hit_ratio == 0.0

    def test_quota_tracker_hit_ratio_progression(self) -> None:
        tracker = ProviderQuotaTracker(daily_limit=100)

        # 1 miss
        tracker.record_call("THYAO")
        assert tracker.get_stats().hit_ratio == 0.0

        # 1 hit -> 1 hit / 2 total = 0.50
        tracker.record_cache_hit("THYAO")
        stats = tracker.get_stats()
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1
        assert stats.hit_ratio == 0.5

        # 3 more hits -> 4 hits / 5 total = 0.80
        for _ in range(3):
            tracker.record_cache_hit("THYAO")
        stats = tracker.get_stats()
        assert stats.cache_hits == 4
        assert stats.cache_misses == 1
        assert stats.hit_ratio == 0.8

    def test_cached_provider_workload_hit_ratio(self) -> None:
        spy = SpyMarketDataProvider()
        cached = CachedMarketDataProvider(inner_provider=spy, daily_quota_limit=50)

        d1 = date(2026, 1, 1)
        d2 = date(2026, 1, 10)

        symbols = ["THYAO", "KCHOL", "ASELS"]
        # Cycle 1: All cold misses (3 calls)
        for sym in symbols:
            cached.fetch_daily_bars(sym, d1, d2)

        assert spy.daily_fetch_calls == 3
        stats_cold = cached.quota_tracker.get_stats()
        assert stats_cold.calls_made == 3
        assert stats_cold.cache_hits == 0
        assert stats_cold.hit_ratio == 0.0

        # Cycle 2-5: 4 repeat rounds for each symbol -> 12 hits, 0 provider calls
        for _ in range(4):
            for sym in symbols:
                cached.fetch_daily_bars(sym, d1, d2)

        assert spy.daily_fetch_calls == 3  # Unchanged!
        stats_warm = cached.quota_tracker.get_stats()
        assert stats_warm.calls_made == 3
        assert stats_warm.cache_hits == 12
        # Total requests = 3 misses + 12 hits = 15; hit ratio = 12 / 15 = 0.80 (80%)
        assert stats_warm.hit_ratio == 0.80


class TestLLMExplainerCacheAndRepetition:
    """Test suite for SafeLLMExplainer cache hit ratio and repetition prevention."""

    def test_explainer_initial_cache_stats(self) -> None:
        explainer = SafeLLMExplainer()
        assert explainer.cache_hits == 0
        assert explainer.cache_misses == 0
        assert explainer.hit_ratio == 0.0

    def test_duplicate_call_prevention_single_llm_invocation(self) -> None:
        """Kabul Testi: Ayni girdi ile n kez cagirildiginda LLM sadece 1 kez calismalidir."""
        call_count = 0

        def counting_generator(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "Piyasa gostergeleri olumlu bolgededir."

        mock_client = MockSafeLLMClient(response_generator=counting_generator)
        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=mock_client)

        indicators = {"rsi_14": 55.4, "sma_20": 150.2}

        # 1. First call: Cache miss -> LLM invoked
        res1, meta1 = explainer.explain(symbol="THYAO", indicators=indicators)
        assert meta1["llm_used"] is True
        assert meta1["cached"] is False
        assert call_count == 1
        assert explainer.cache_hits == 0
        assert explainer.cache_misses == 1

        # 2-5. Repeated calls with identical parameters: Cache hit -> LLM bypassed
        for _ in range(4):
            res_repeat, meta_repeat = explainer.explain(symbol="THYAO", indicators=indicators)
            assert meta_repeat["llm_used"] is False
            assert meta_repeat["cached"] is True
            assert meta_repeat["reason"] == "CACHE_HIT"
            assert res_repeat.context_summary == res1.context_summary

        assert call_count == 1  # Exactly 1 call regardless of 5 requests!
        assert explainer.cache_hits == 4
        assert explainer.cache_misses == 1
        assert explainer.hit_ratio == 0.80  # 4 / 5 = 80%

    def test_cache_key_normalization_prevents_duplicate_calls(self) -> None:
        """Symbol casing and extra whitespace should normalize to the same cache key."""
        mock_client = MagicMock()
        mock_client.generate.return_value = "Teknik gostergeler stabil seyretmektedir."

        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=mock_client)

        indicators = {"rsi_14": 48.0}

        # Call with lowercase and whitespace
        explainer.explain(
            symbol="  thyao  ",
            indicators=indicators,
            external_context=" Haber verisi  ",
        )
        assert mock_client.generate.call_count == 1

        # Call with uppercase and trimmed context
        explainer.explain(symbol="THYAO", indicators=indicators, external_context="Haber verisi")
        # Should hit cache and NOT invoke LLM client again
        assert mock_client.generate.call_count == 1
        assert explainer.cache_hits == 1

    def test_indicator_change_causes_cache_miss(self) -> None:
        """When indicators differ, a fresh LLM call is required."""
        call_count = 0

        def counting_generator(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return f"Analiz guncellemesi #{call_count}"

        mock_client = MockSafeLLMClient(response_generator=counting_generator)
        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=mock_client)

        # Call 1: RSI 30
        explainer.explain(symbol="BIMAS", indicators={"rsi_14": 30.0})
        assert call_count == 1

        # Call 2: RSI 70 (different indicators -> must miss and re-call LLM)
        explainer.explain(symbol="BIMAS", indicators={"rsi_14": 70.0})
        assert call_count == 2
        assert explainer.cache_misses == 2
        assert explainer.cache_hits == 0
        assert explainer.hit_ratio == 0.0

    def test_cache_ttl_expiration(self) -> None:
        """Expired cache items must trigger a new LLM generation."""
        mock_client = MagicMock()
        mock_client.generate.return_value = "Guncel teknik ozet."

        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        # Short TTL of 1 second
        explainer = SafeLLMExplainer(
            settings=settings,
            llm_client=mock_client,
            cache_ttl_seconds=1,
        )

        indicators = {"rsi_14": 50.0}

        # First call: misses cache
        explainer.explain(symbol="SISE", indicators=indicators)
        assert mock_client.generate.call_count == 1
        assert explainer.cache_misses == 1

        # Immediate second call: hits cache
        explainer.explain(symbol="SISE", indicators=indicators)
        assert mock_client.generate.call_count == 1
        assert explainer.cache_hits == 1

        # Artificially age the cache entry beyond 1 second TTL
        for key, (t, val) in explainer._cache.items():
            aged_time = datetime.fromtimestamp(t.timestamp() - 10, tz=UTC)
            explainer._cache[key] = (aged_time, val)

        # Third call: expired -> misses cache and calls LLM
        explainer.explain(symbol="SISE", indicators=indicators)
        assert mock_client.generate.call_count == 2
        assert explainer.cache_misses == 2
        assert explainer.cache_hits == 1
        assert round(explainer.hit_ratio, 2) == 0.33

    def test_clear_cache_resets_state(self) -> None:
        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings)

        explainer.explain(symbol="AKBNK", indicators={"rsi_14": 60.0})
        explainer.explain(symbol="AKBNK", indicators={"rsi_14": 60.0})

        assert explainer.cache_hits == 1
        assert explainer.cache_misses == 1
        assert len(explainer._cache) == 1

        explainer.clear_cache()
        assert explainer.cache_hits == 0
        assert explainer.cache_misses == 0
        assert explainer.hit_ratio == 0.0
        assert len(explainer._cache) == 0

    def test_unsafe_responses_are_not_cached_as_valid(self) -> None:
        """Responses with banned phrases trigger fallback and should NOT poison the cache."""
        mock_client = MockSafeLLMClient(
            response_generator=lambda _: "Bu hisseyi kesinlikle al ve bekle."
        )
        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=mock_client)

        indicators = {"rsi_14": 65.0}

        # First call: triggers safety violation fallback
        res1, meta1 = explainer.explain(symbol="FROTO", indicators=indicators)
        assert meta1["fallback_used"] is True
        assert "BANNED_PHRASE_DETECTED" in meta1["reason"]

        # Cache must not contain the banned generation
        assert len(explainer._cache) == 0

    def test_provider_failure_does_not_poison_cache(self) -> None:
        """If external LLM raises an exception, the error fallback must not be cached."""
        failing_client = MagicMock()
        failing_client.generate.side_effect = RuntimeError("Provider timeout")

        settings = Settings(llm_enabled=True)  # type: ignore[call-arg]
        explainer = SafeLLMExplainer(settings=settings, llm_client=failing_client)

        res, meta = explainer.explain(symbol="TUPRS", indicators={"rsi_14": 40.0})
        assert meta["fallback_used"] is True
        assert "LLM_PROVIDER_ERROR" in meta["reason"]
        assert len(explainer._cache) == 0
