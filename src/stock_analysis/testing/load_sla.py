"""Load testing metrics, SLA evaluator, and benchmark reporting against ACCEPTANCE_CRITERIA.md.

Acceptance Criteria (docs/ACCEPTANCE_CRITERIA.md):
- API (önbellekli okuma): p95 yanıt < 300 ms
- API (önbelleksiz okuma): p95 yanıt < 800 ms
- Hata Oranı: 500 İç Sunucu Hatası = 0, Kritik/yüksek açık = 0
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


# SLA Thresholds from docs/ACCEPTANCE_CRITERIA.md
SLA_P95_CACHED_MAX_MS: float = 300.0
SLA_P95_UNCACHED_MAX_MS: float = 800.0
SLA_MAX_ERROR_RATE_PCT: float = 0.0


@dataclasses.dataclass(frozen=True)
class EndpointBenchmarkResult:
    """Benchmark metrics for a single endpoint category."""

    endpoint_name: str
    category: str  # 'cached' or 'uncached'
    sample_count: int
    min_ms: float
    max_ms: float
    mean_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    error_count: int
    sla_threshold_ms: float
    is_sla_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_name": self.endpoint_name,
            "category": self.category,
            "sample_count": self.sample_count,
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "mean_ms": round(self.mean_ms, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p90_ms": round(self.p90_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "error_count": self.error_count,
            "sla_threshold_ms": self.sla_threshold_ms,
            "is_sla_passed": self.is_sla_passed,
        }


@dataclasses.dataclass(frozen=True)
class LoadTestVerdict:
    """Consolidated verdict for the load test run against ACCEPTANCE_CRITERIA.md."""

    total_requests: int
    total_errors: int
    overall_p95_cached_ms: float
    overall_p95_uncached_ms: float
    is_cached_sla_met: bool
    is_uncached_sla_met: bool
    is_error_rate_sla_met: bool
    all_passed: bool
    endpoint_results: list[EndpointBenchmarkResult]
    summary_message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "overall_p95_cached_ms": round(self.overall_p95_cached_ms, 2),
            "overall_p95_uncached_ms": round(self.overall_p95_uncached_ms, 2),
            "is_cached_sla_met": self.is_cached_sla_met,
            "is_uncached_sla_met": self.is_uncached_sla_met,
            "is_error_rate_sla_met": self.is_error_rate_sla_met,
            "all_passed": self.all_passed,
            "summary_message": self.summary_message,
            "endpoints": [ep.to_dict() for ep in self.endpoint_results],
        }


def calculate_percentile(latencies: list[float], percentile: float) -> float:
    """Calculate the given percentile value from a list of latencies in milliseconds."""
    if not latencies:
        return 0.0
    sorted_vals = sorted(latencies)
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (percentile / 100.0) * (len(sorted_vals) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_vals[int(rank)]
    weight = rank - lower
    return sorted_vals[lower] * (1.0 - weight) + sorted_vals[upper] * weight


def evaluate_sla_compliance(
    cached_latencies_ms: list[float],
    uncached_latencies_ms: list[float],
    cached_errors: int = 0,
    uncached_errors: int = 0,
    endpoint_name_cached: str = "GET /api/v1/market/symbols (cached)",
    endpoint_name_uncached: str = "GET /api/v1/market/history (uncached)",
) -> LoadTestVerdict:
    """Compare observed response times against docs/ACCEPTANCE_CRITERIA.md p95 limits."""
    # Cached metrics
    p95_cached = calculate_percentile(cached_latencies_ms, 95.0)
    cached_passed = p95_cached < SLA_P95_CACHED_MAX_MS and cached_errors == 0
    cached_ep = EndpointBenchmarkResult(
        endpoint_name=endpoint_name_cached,
        category="cached",
        sample_count=len(cached_latencies_ms),
        min_ms=min(cached_latencies_ms) if cached_latencies_ms else 0.0,
        max_ms=max(cached_latencies_ms) if cached_latencies_ms else 0.0,
        mean_ms=sum(cached_latencies_ms) / len(cached_latencies_ms) if cached_latencies_ms else 0.0,
        p50_ms=calculate_percentile(cached_latencies_ms, 50.0),
        p90_ms=calculate_percentile(cached_latencies_ms, 90.0),
        p95_ms=p95_cached,
        p99_ms=calculate_percentile(cached_latencies_ms, 99.0),
        error_count=cached_errors,
        sla_threshold_ms=SLA_P95_CACHED_MAX_MS,
        is_sla_passed=cached_passed,
    )

    # Uncached metrics
    p95_uncached = calculate_percentile(uncached_latencies_ms, 95.0)
    uncached_passed = p95_uncached < SLA_P95_UNCACHED_MAX_MS and uncached_errors == 0
    uncached_ep = EndpointBenchmarkResult(
        endpoint_name=endpoint_name_uncached,
        category="uncached",
        sample_count=len(uncached_latencies_ms),
        min_ms=min(uncached_latencies_ms) if uncached_latencies_ms else 0.0,
        max_ms=max(uncached_latencies_ms) if uncached_latencies_ms else 0.0,
        mean_ms=sum(uncached_latencies_ms) / len(uncached_latencies_ms)
        if uncached_latencies_ms
        else 0.0,
        p50_ms=calculate_percentile(uncached_latencies_ms, 50.0),
        p90_ms=calculate_percentile(uncached_latencies_ms, 90.0),
        p95_ms=p95_uncached,
        p99_ms=calculate_percentile(uncached_latencies_ms, 99.0),
        error_count=uncached_errors,
        sla_threshold_ms=SLA_P95_UNCACHED_MAX_MS,
        is_sla_passed=uncached_passed,
    )

    total_reqs = len(cached_latencies_ms) + len(uncached_latencies_ms)
    total_errs = cached_errors + uncached_errors
    errors_passed = total_errs == 0
    all_passed = cached_passed and uncached_passed and errors_passed

    if all_passed:
        summary = (
            f"PASSED: Cached p95 ({p95_cached:.1f}ms < {SLA_P95_CACHED_MAX_MS}ms) and "
            f"Uncached p95 ({p95_uncached:.1f}ms < {SLA_P95_UNCACHED_MAX_MS}ms) met SLA. "
            f"Total requests: {total_reqs}, Errors: {total_errs}."
        )
    else:
        reasons: list[str] = []
        if not cached_passed:
            reasons.append(
                f"Cached p95 ({p95_cached:.1f}ms >= {SLA_P95_CACHED_MAX_MS}ms "
                f"or {cached_errors} errs)"
            )
        if not uncached_passed:
            reasons.append(
                f"Uncached p95 ({p95_uncached:.1f}ms >= {SLA_P95_UNCACHED_MAX_MS}ms "
                f"or {uncached_errors} errs)"
            )

        if total_errs > 0:
            reasons.append(f"Errors detected: {total_errs}")
        summary = f"FAILED SLA: {'; '.join(reasons)}"

    return LoadTestVerdict(
        total_requests=total_reqs,
        total_errors=total_errs,
        overall_p95_cached_ms=p95_cached,
        overall_p95_uncached_ms=p95_uncached,
        is_cached_sla_met=cached_passed,
        is_uncached_sla_met=uncached_passed,
        is_error_rate_sla_met=errors_passed,
        all_passed=all_passed,
        endpoint_results=[cached_ep, uncached_ep],
        summary_message=summary,
    )


# ---------------------------------------------------------------------------
# Load Testing User & Task Definitions
# ---------------------------------------------------------------------------


def task(weight: int = 1) -> Callable[[F], F]:
    """Decorator marking a method as a benchmark load test task with execution weight."""

    def decorator(func: F) -> F:
        func.locust_weight = weight  # type: ignore[attr-defined]
        return func

    return decorator


def between(min_wait: float, max_wait: float) -> Callable[[], float]:
    """Return a wait time generator function between min and max delay."""
    return lambda: (min_wait + max_wait) / 2.0


class BaseLoadUser:
    """Base user class for simulated client sessions compatible with Locust and headless runners."""

    abstract: bool = True
    client: Any = None
    wait_time: Callable[[], float] | None = None


class BorsaAnalizUser(BaseLoadUser):
    """Simulates realistic investor queries across public and technical analysis APIs."""

    wait_time = between(0.05, 0.2)

    @task(5)
    def query_cached_symbols(self) -> None:
        """High-frequency cached symbol browsing (SLA: p95 < 300ms)."""
        if self.client:
            self.client.get(
                "/api/v1/market/symbols?page=1&page_size=20",
                name="[Cached] GET /symbols",
            )

    @task(3)
    def query_cached_history(self) -> None:
        """Cached 1-month daily candle chart view (SLA: p95 < 300ms)."""
        if self.client:
            self.client.get(
                "/api/v1/market/history/THYAO?range=1m&interval=1d&price_type=adjusted",
                name="[Cached] GET /history/THYAO (1m)",
            )

    @task(2)
    def query_uncached_history(self) -> None:
        """Uncached multi-year historical data query with DB lookup (SLA: p95 < 800ms)."""
        if self.client:
            self.client.get(
                "/api/v1/market/history/ASELS?range=5y&interval=1d&price_type=adjusted",
                name="[Uncached] GET /history/ASELS (5y)",
            )

    @task(2)
    def query_downsampled_chart(self) -> None:
        """Downsampled chart query for fast mobile/web rendering."""
        if self.client:
            self.client.get(
                "/api/v1/market/history/GARAN?range=1y&interval=1d&downsample=bucket&max_points=50",
                name="[Downsample] GET /history/GARAN (bucket)",
            )

    @task(1)
    def query_health_check(self) -> None:
        """Liveness probe monitoring."""
        if self.client:
            self.client.get(
                "/health",
                name="[System] GET /health",
            )
