"""Tests for market data provider resilience, bounded retry, and fault tolerance."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    DataStatus,
    MarketBarRecord,
    ProviderDataFormatError,
    ProviderHealth,
    ProviderQuotaExceededError,
    ProviderSchemaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from stock_analysis.providers.resilience import (
    ResilientMarketDataProvider,
    RetryPolicy,
    execute_with_retry,
)


class MockFlakyProvider(BaseMarketDataProvider):
    """Mock provider with controllable failure behaviors for testing resilience."""

    def __init__(self, failure_sequence: list[Exception | Any]) -> None:
        self._sequence = list(failure_sequence)
        self.call_count = 0

    @property
    def name(self) -> str:
        return "mock_flaky"

    @property
    def is_licensed(self) -> bool:
        return True

    def _next_behavior(self) -> Any:
        self.call_count += 1
        if not self._sequence:
            return None
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        res = self._next_behavior()
        return res if isinstance(res, list) else []

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        res = self._next_behavior()
        return res if isinstance(res, MarketBarRecord) else None

    def get_supported_symbols(self) -> list[str]:
        res = self._next_behavior()
        return res if isinstance(res, list) else ["THYAO"]

    def health_check(self) -> ProviderHealth:
        res = self._next_behavior()
        if isinstance(res, ProviderHealth):
            return res
        return ProviderHealth(is_healthy=True, provider_name=self.name, is_licensed=True)


def test_retry_policy_delay_calculation() -> None:
    """Verify exponential backoff calculation and upper bound enforcement."""
    policy = RetryPolicy(
        max_retries=3,
        initial_delay_sec=1.0,
        max_delay_sec=5.0,
        backoff_factor=2.0,
        jitter=False,
    )

    # Attempt 0: 1.0 * (2^0) = 1.0
    assert policy.calculate_delay(0) == 1.0
    # Attempt 1: 1.0 * (2^1) = 2.0
    assert policy.calculate_delay(1) == 2.0
    # Attempt 2: 1.0 * (2^2) = 4.0
    assert policy.calculate_delay(2) == 4.0
    # Attempt 3: 1.0 * (2^3) = 8.0 -> capped at max_delay_sec 5.0
    assert policy.calculate_delay(3) == 5.0


def test_retry_policy_with_jitter() -> None:
    """Verify that jitter generates delays within [0.5 * base, base]."""
    policy = RetryPolicy(
        max_retries=3,
        initial_delay_sec=2.0,
        max_delay_sec=10.0,
        backoff_factor=2.0,
        jitter=True,
    )
    for _ in range(10):
        delay = policy.calculate_delay(0)  # base is 2.0
        assert 1.0 <= delay <= 2.0


def test_execute_with_retry_immediate_success() -> None:
    """Verify operation succeeds immediately on attempt 0 without sleeping."""
    sleep_calls: list[float] = []

    result = execute_with_retry(
        lambda: 42,
        policy=RetryPolicy(max_retries=3),
        sleeper=sleep_calls.append,
    )
    assert result == 42
    assert len(sleep_calls) == 0


def test_execute_with_retry_transient_recovery() -> None:
    """Verify recovery after transient errors with bounded backoff."""
    sleep_calls: list[float] = []
    attempts = 0

    def flaky_op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderTimeoutError("Gateway timeout")
        if attempts == 2:
            raise ProviderUnavailableError("Connection reset")
        return "SUCCESS"

    result = execute_with_retry(
        flaky_op,
        policy=RetryPolicy(max_retries=3, initial_delay_sec=0.1, jitter=False),
        sleeper=sleep_calls.append,
    )

    assert result == "SUCCESS"
    assert attempts == 3
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == 0.1
    assert sleep_calls[1] == 0.2


def test_execute_with_retry_exhaustion() -> None:
    """Verify that when all retries are exhausted, exception is raised."""
    sleep_calls: list[float] = []
    attempts = 0

    def failing_op() -> None:
        nonlocal attempts
        attempts += 1
        raise ProviderUnavailableError("Service permanently down")

    with pytest.raises(ProviderUnavailableError, match="Service permanently down"):
        execute_with_retry(
            failing_op,
            policy=RetryPolicy(max_retries=2, initial_delay_sec=0.05, jitter=False),
            sleeper=sleep_calls.append,
        )

    assert attempts == 3  # Initial + 2 retries
    assert len(sleep_calls) == 2


def test_execute_with_retry_fail_fast_on_quota_exceeded() -> None:
    """Verify fail-fast behavior: quota exceeded errors must never be retried."""
    sleep_calls: list[float] = []
    attempts = 0

    def quota_failing_op() -> None:
        nonlocal attempts
        attempts += 1
        raise ProviderQuotaExceededError(
            "Daily limit reached",
            quota_limit=500,
            retry_after_sec=3600,
        )

    with pytest.raises(ProviderQuotaExceededError) as exc_info:
        execute_with_retry(
            quota_failing_op,
            policy=RetryPolicy(max_retries=3),
            sleeper=sleep_calls.append,
        )

    assert attempts == 1  # Never retried!
    assert len(sleep_calls) == 0
    assert exc_info.value.quota_limit == 500
    assert exc_info.value.retry_after_sec == 3600


def test_execute_with_retry_fail_fast_on_schema_and_format_error() -> None:
    """Verify fail-fast on schema or corrupted data format errors."""
    sleep_calls: list[float] = []
    attempts = 0

    def schema_failing_op() -> None:
        nonlocal attempts
        attempts += 1
        raise ProviderSchemaError("Missing field 'raw_close'", missing_field="raw_close")

    with pytest.raises(ProviderSchemaError) as exc_info:
        execute_with_retry(
            schema_failing_op,
            policy=RetryPolicy(max_retries=3),
            sleeper=sleep_calls.append,
        )

    assert attempts == 1
    assert len(sleep_calls) == 0
    assert exc_info.value.missing_field == "raw_close"

    # Also check ProviderDataFormatError
    def corrupt_format_op() -> None:
        raise ProviderDataFormatError("Corrupted JSON response")

    with pytest.raises(ProviderDataFormatError):
        execute_with_retry(
            corrupt_format_op,
            policy=RetryPolicy(max_retries=3),
            sleeper=sleep_calls.append,
        )
    assert len(sleep_calls) == 0


def test_resilient_provider_wrapper_bars_fetch() -> None:
    """Verify ResilientMarketDataProvider delegates and retries properly."""
    sample_bar = MarketBarRecord(
        symbol="THYAO",
        trade_date=date(2026, 1, 5),
        data_status=DataStatus.VERIFIED,
        raw_open=Decimal("100.0"),
        raw_high=Decimal("105.0"),
        raw_low=Decimal("98.0"),
        raw_close=Decimal("102.0"),
        adj_open=Decimal("100.0"),
        adj_high=Decimal("105.0"),
        adj_low=Decimal("98.0"),
        adj_close=Decimal("102.0"),
        volume=1000,
        turnover_try=Decimal("102000.0"),
        source_name="mock_source",
    )

    sleep_calls: list[float] = []
    flaky = MockFlakyProvider(
        [
            ProviderTimeoutError("Socket timeout"),
            [sample_bar],
        ]
    )

    resilient = ResilientMarketDataProvider(
        inner_provider=flaky,
        policy=RetryPolicy(max_retries=2, initial_delay_sec=0.01, jitter=False),
        sleeper=sleep_calls.append,
    )

    assert resilient.name == "resilient(mock_flaky)"
    assert resilient.is_licensed is True

    bars = resilient.fetch_daily_bars("THYAO", date(2026, 1, 1), date(2026, 1, 10))
    assert len(bars) == 1
    assert bars[0].symbol == "THYAO"
    assert flaky.call_count == 2
    assert len(sleep_calls) == 1
    assert resilient.last_latency_ms is not None
    assert resilient.total_invocations == 1
    assert resilient.failed_invocations == 0


def test_resilient_provider_health_check() -> None:
    """Verify ResilientMarketDataProvider health_check handles healthy and error states."""
    # 1. Healthy state
    flaky_ok = MockFlakyProvider([])
    resilient_ok = ResilientMarketDataProvider(inner_provider=flaky_ok)
    health = resilient_ok.health_check()
    assert health.is_healthy is True
    assert health.provider_name == "resilient(mock_flaky)"
    assert health.latency_ms is not None
    assert health.latency_ms >= 0

    # 2. Quota exceeded state in health check
    flaky_quota = MockFlakyProvider(
        [
            ProviderQuotaExceededError("Rate limit reached", quota_limit=1000),
        ]
    )
    resilient_quota = ResilientMarketDataProvider(inner_provider=flaky_quota)
    health_quota = resilient_quota.health_check()
    assert health_quota.is_healthy is False
    assert health_quota.quota_remaining == 0
    assert health_quota.quota_limit == 1000
    assert "Quota exceeded" in health_quota.message
