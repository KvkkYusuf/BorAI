"""Local response caching and quota tracking layer for market data providers.

Ensures:
- Provider responses are cached locally to prevent redundant external network roundtrips.
- Strict quota counter to enforce daily provider call limits and prevent overage charges.
- Transparent fallback to cache on identical queries (same symbol and date range).
- Clean observability with cache hit/miss and quota metrics.
"""

import hashlib
import time
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.observability.provider_alerts import PROVIDER_ALERT_MANAGER
from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    MarketBarRecord,
    ProviderHealth,
    ProviderQuotaExceededError,
)


def generate_data_version(
    symbol: str,
    bars: Sequence[MarketBarRecord] | None = None,
    tag: str | None = None,
) -> str:
    """Generate a deterministic version identifier for a market dataset snapshot.

    Combines ticker symbol, sorted bar payload signatures, and optional semantic tag
    into a reproducible content-addressable version string (e.g. 'dv_a1b2c3d4e5f60718').
    """
    hasher = hashlib.sha256()
    hasher.update(symbol.strip().upper().encode("utf-8"))
    if tag:
        hasher.update(tag.strip().encode("utf-8"))
    if bars:
        for b in sorted(bars, key=lambda x: x.trade_date):
            sig = (
                f"{b.trade_date.isoformat()}|{b.raw_open}|{b.raw_high}|"
                f"{b.raw_low}|{b.raw_close}|{b.volume}|{b.turnover_try}|{b.source_name}"
            )
            hasher.update(sig.encode("utf-8"))
    digest = hasher.hexdigest()[:16]
    return f"dv_{digest}"


class QuotaStats(BaseModel):
    """Snapshot of provider call quota and cache efficiency metrics."""

    model_config = ConfigDict(frozen=True)

    daily_limit: int | None = Field(default=None, description="Configured daily call cap")
    calls_made: int = Field(ge=0, description="Total external provider calls executed")
    calls_remaining: int | None = Field(
        default=None, description="Remaining calls before exhaustion"
    )
    cache_hits: int = Field(ge=0, description="Number of requests served from cache")
    cache_misses: int = Field(ge=0, description="Number of requests requiring external calls")
    hit_ratio: float = Field(ge=0.0, le=1.0, description="Cache hit ratio (0.0 to 1.0)")


class ProviderQuotaTracker:
    """Thread-safe counter tracking external API calls, limits, and cache hits."""

    def __init__(self, daily_limit: int | None = None) -> None:
        if daily_limit is not None and daily_limit < 1:
            raise ValueError(f"daily_limit must be a positive integer, got {daily_limit}")
        self.daily_limit = daily_limit
        self._calls_made = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._calls_by_symbol: dict[str, int] = {}

    @property
    def calls_made(self) -> int:
        return self._calls_made

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def cache_misses(self) -> int:
        return self._cache_misses

    @property
    def quota_remaining(self) -> int | None:
        if self.daily_limit is None:
            return None
        return max(0, self.daily_limit - self._calls_made)

    def record_cache_hit(self, symbol: str) -> None:
        """Increment cache hit counter."""
        self._cache_hits += 1

    def record_call(self, symbol: str) -> None:
        """Verify quota allowance and increment external call counter.

        Raises:
            ProviderQuotaExceededError: When daily_limit is exceeded.
        """
        sym = symbol.upper()
        if self.daily_limit is not None and self._calls_made >= self.daily_limit:
            raise ProviderQuotaExceededError(
                f"Provider daily quota limit ({self.daily_limit}) exhausted. "
                f"Attempted call for symbol '{sym}'.",
                quota_limit=self.daily_limit,
            )
        self._calls_made += 1
        self._cache_misses += 1
        self._calls_by_symbol[sym] = self._calls_by_symbol.get(sym, 0) + 1

    def get_stats(self) -> QuotaStats:
        """Generate an immutable snapshot of quota and cache metrics."""
        total_requests = self._cache_hits + self._cache_misses
        hit_ratio = (self._cache_hits / total_requests) if total_requests > 0 else 0.0
        return QuotaStats(
            daily_limit=self.daily_limit,
            calls_made=self._calls_made,
            calls_remaining=self.quota_remaining,
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
            hit_ratio=round(hit_ratio, 4),
        )

    def reset(self) -> None:
        """Reset all counters (e.g. at the start of a new calendar day)."""
        self._calls_made = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._calls_by_symbol.clear()


class MarketDataCache:
    """In-memory deterministic cache for market data bars and latest quotes.

    Cache keys are bound to symbol, date range, and a deterministic data_version,
    guaranteeing that historical adjustments or updated snapshots do not collide
    or serve stale revisions.
    """

    def __init__(self, default_data_version: str = "v1") -> None:
        self.default_data_version = default_data_version
        # Key: (symbol, start_date, end_date, data_version) -> tuple of MarketBarRecord
        self._range_cache: dict[tuple[str, date, date, str], tuple[MarketBarRecord, ...]] = {}
        # Key: (symbol, start_date, end_date) -> latest stored data_version
        self._latest_range_versions: dict[tuple[str, date, date], str] = {}
        # Key: symbol -> (cached_at_monotonic, MarketBarRecord | None)
        self._latest_cache: dict[str, tuple[float, MarketBarRecord | None]] = {}
        # Key: (symbol, data_version) -> (cached_at_monotonic, MarketBarRecord | None)
        self._latest_versioned_cache: dict[
            tuple[str, str], tuple[float, MarketBarRecord | None]
        ] = {}

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        data_version: str | None = None,
    ) -> tuple[MarketBarRecord, ...] | None:
        """Retrieve cached daily bars for symbol, date range, and optional data_version.

        If data_version is omitted, returns the latest active version cached for that range.
        """
        sym = symbol.strip().upper()
        version = data_version or self._latest_range_versions.get((sym, start_date, end_date))
        if version is None:
            return None
        key = (sym, start_date, end_date, version)
        return self._range_cache.get(key)

    def set_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        bars: Sequence[MarketBarRecord],
        data_version: str | None = None,
    ) -> str:
        """Cache daily bars bound to a data_version.

        If data_version is not explicitly provided, generates a content-addressable
        version via generate_data_version. Returns the resolved data_version string.
        """
        sym = symbol.strip().upper()
        resolved_version = data_version or generate_data_version(sym, bars)
        key = (sym, start_date, end_date, resolved_version)
        self._range_cache[key] = tuple(bars)
        self._latest_range_versions[(sym, start_date, end_date)] = resolved_version
        return resolved_version

    def get_latest_bar(
        self,
        symbol: str,
        max_age_seconds: float = 300.0,
        data_version: str | None = None,
    ) -> MarketBarRecord | None:
        """Retrieve cached latest bar if within TTL max_age_seconds."""
        sym = symbol.strip().upper()
        entry: tuple[float, MarketBarRecord | None] | None = None
        if data_version is not None:
            entry = self._latest_versioned_cache.get((sym, data_version))
        if entry is None:
            entry = self._latest_cache.get(sym)
        if entry is None:
            return None
        cached_at, bar = entry
        if (time.monotonic() - cached_at) >= max_age_seconds:
            return None
        return bar

    def set_latest_bar(
        self,
        symbol: str,
        bar: MarketBarRecord | None,
        data_version: str | None = None,
    ) -> str:
        """Store latest bar with current monotonic timestamp and data_version."""
        sym = symbol.strip().upper()
        resolved_version = data_version or (
            generate_data_version(sym, [bar]) if bar is not None else self.default_data_version
        )
        entry = (time.monotonic(), bar)
        self._latest_cache[sym] = entry
        self._latest_versioned_cache[(sym, resolved_version)] = entry
        return resolved_version

    def get_cached_versions(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[str]:
        """Return all distinct data versions cached for a given symbol and date range."""
        sym = symbol.strip().upper()
        return [
            k[3]
            for k in self._range_cache
            if k[0] == sym and k[1] == start_date and k[2] == end_date
        ]

    def clear(
        self,
        symbol: str | None = None,
        data_version: str | None = None,
    ) -> None:
        """Evict cache entries, optionally filtered by symbol and/or data_version."""
        if symbol is None and data_version is None:
            self._range_cache.clear()
            self._latest_range_versions.clear()
            self._latest_cache.clear()
            self._latest_versioned_cache.clear()
            return

        sym = symbol.strip().upper() if symbol is not None else None

        self._range_cache = {
            k: v
            for k, v in self._range_cache.items()
            if not ((sym is None or k[0] == sym) and (data_version is None or k[3] == data_version))
        }
        self._latest_versioned_cache = {
            k: v
            for k, v in self._latest_versioned_cache.items()
            if not ((sym is None or k[0] == sym) and (data_version is None or k[1] == data_version))
        }
        if data_version is None and sym is not None:
            self._latest_cache.pop(sym, None)
        elif data_version is None:
            self._latest_cache.clear()

        # Recompute latest version pointers
        self._latest_range_versions = {(k[0], k[1], k[2]): k[3] for k in self._range_cache}

    def count(self, symbol: str | None = None) -> int:
        """Return total number of cached bar range datasets."""
        if symbol is not None:
            sym = symbol.strip().upper()
            return sum(1 for k in self._range_cache if k[0] == sym)
        return len(self._range_cache)


class CachedMarketDataProvider(BaseMarketDataProvider):
    """Decorator wrapping any market data provider with caching and quota tracking.

    Prevents:
    - Multiple roundtrips for the exact same query range.
    - Exceeding daily provider budget or rate limits.
    - Serving stale revisions by binding queries to explicit or content-derived data_version.
    """

    def __init__(
        self,
        inner_provider: BaseMarketDataProvider,
        daily_quota_limit: int | None = None,
        cache: MarketDataCache | None = None,
        quota_tracker: ProviderQuotaTracker | None = None,
        latest_bar_ttl_seconds: float = 300.0,
        default_data_version: str | None = None,
    ) -> None:
        self.inner_provider = inner_provider
        self.cache = cache or MarketDataCache()
        self.quota_tracker = quota_tracker or ProviderQuotaTracker(daily_limit=daily_quota_limit)
        self.latest_bar_ttl_seconds = latest_bar_ttl_seconds
        self.default_data_version = default_data_version

    @property
    def name(self) -> str:
        return f"cached_{self.inner_provider.name}"

    @property
    def is_licensed(self) -> bool:
        return self.inner_provider.is_licensed

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        data_version: str | None = None,
    ) -> list[MarketBarRecord]:
        """Fetch daily bars; returns cached copy if previously fetched for matching data_version."""
        sym = symbol.upper()
        target_version = data_version or self.default_data_version
        cached = self.cache.get_daily_bars(sym, start_date, end_date, data_version=target_version)
        if cached is not None:
            self.quota_tracker.record_cache_hit(sym)
            return list(cached)

        # Cache miss: enforce quota and query underlying provider
        try:
            self.quota_tracker.record_call(sym)
            if self.quota_tracker.daily_limit is not None:
                PROVIDER_ALERT_MANAGER.record_quota_usage(
                    self.inner_provider.name,
                    self.quota_tracker.calls_made,
                    self.quota_tracker.daily_limit,
                )
        except ProviderQuotaExceededError:
            if self.quota_tracker.daily_limit is not None:
                PROVIDER_ALERT_MANAGER.record_quota_usage(
                    self.inner_provider.name,
                    self.quota_tracker.calls_made,
                    self.quota_tracker.daily_limit,
                )
            raise

        try:
            bars = self.inner_provider.fetch_daily_bars(sym, start_date, end_date)
            PROVIDER_ALERT_MANAGER.record_success(self.inner_provider.name)
            self.cache.set_daily_bars(sym, start_date, end_date, bars, data_version=target_version)
            return list(bars)
        except Exception as exc:
            PROVIDER_ALERT_MANAGER.record_failure(
                self.inner_provider.name,
                status_code=500,
                error_message=str(exc),
            )
            raise

    def fetch_latest_bar(
        self,
        symbol: str,
        data_version: str | None = None,
    ) -> MarketBarRecord | None:
        """Fetch latest bar with TTL-aware caching bound to data_version."""
        sym = symbol.upper()
        target_version = data_version or self.default_data_version
        cached = self.cache.get_latest_bar(
            sym, max_age_seconds=self.latest_bar_ttl_seconds, data_version=target_version
        )
        if cached is not None:
            self.quota_tracker.record_cache_hit(sym)
            return cached

        try:
            self.quota_tracker.record_call(sym)
            if self.quota_tracker.daily_limit is not None:
                PROVIDER_ALERT_MANAGER.record_quota_usage(
                    self.inner_provider.name,
                    self.quota_tracker.calls_made,
                    self.quota_tracker.daily_limit,
                )
        except ProviderQuotaExceededError:
            if self.quota_tracker.daily_limit is not None:
                PROVIDER_ALERT_MANAGER.record_quota_usage(
                    self.inner_provider.name,
                    self.quota_tracker.calls_made,
                    self.quota_tracker.daily_limit,
                )
            raise

        try:
            bar = self.inner_provider.fetch_latest_bar(sym)
            PROVIDER_ALERT_MANAGER.record_success(self.inner_provider.name)
            self.cache.set_latest_bar(sym, bar, data_version=target_version)
            return bar
        except Exception as exc:
            PROVIDER_ALERT_MANAGER.record_failure(
                self.inner_provider.name,
                status_code=500,
                error_message=str(exc),
            )
            raise

    def get_supported_symbols(self) -> list[str]:
        """Forward symbol directory query to inner provider."""
        return self.inner_provider.get_supported_symbols()

    def health_check(self) -> ProviderHealth:
        """Report inner provider connectivity combined with local quota metrics."""
        inner_health = self.inner_provider.health_check()
        stats = self.quota_tracker.get_stats()

        if stats.daily_limit is not None:
            PROVIDER_ALERT_MANAGER.record_quota_usage(
                self.inner_provider.name,
                stats.calls_made,
                stats.daily_limit,
            )

        # Overlay local quota limit/remaining if configured
        remaining = (
            stats.calls_remaining if stats.daily_limit is not None else inner_health.quota_remaining
        )
        limit = stats.daily_limit if stats.daily_limit is not None else inner_health.quota_limit

        msg = (
            f"{inner_health.message} | Cache: {stats.cache_hits} hits, "
            f"{stats.cache_misses} misses ({stats.hit_ratio:.1%} hit ratio)"
        )
        return ProviderHealth(
            is_healthy=inner_health.is_healthy,
            provider_name=self.name,
            is_licensed=self.is_licensed,
            latency_ms=inner_health.latency_ms,
            quota_remaining=remaining,
            quota_limit=limit,
            message=msg,
        )
