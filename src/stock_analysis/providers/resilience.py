"""Resilience, retry policies, and fault-tolerance wrappers for market data providers.

Enforces:
- Bounded exponential backoff with jitter (no infinite retries).
- Explicit, typed failure handling: fail-fast on non-retryable errors (quota exhaustion,
  schema violation, auth failures).
- Retry on transient errors (timeouts, network drops, provider unavailable).
- No silent fallbacks: errors are never silently swallowed or masked.
"""

import logging
import random
import time
from collections.abc import Callable, Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    MarketBarRecord,
    ProviderDataFormatError,
    ProviderError,
    ProviderHealth,
    ProviderQuotaExceededError,
    ProviderSchemaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class RetryPolicy(BaseModel):
    """Configuration for bounded exponential backoff with jitter."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=3, ge=1, le=10, description="Max retry attempts (1-10)")
    initial_delay_sec: float = Field(
        default=0.5,
        gt=0.0,
        le=30.0,
        description="Initial retry backoff delay in seconds",
    )
    max_delay_sec: float = Field(
        default=5.0,
        gt=0.0,
        le=60.0,
        description="Upper bound for retry delay in seconds",
    )
    backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=5.0,
        description="Multiplicative backoff factor per attempt",
    )
    jitter: bool = Field(default=True, description="Add random jitter to mitigate thundering herds")

    def calculate_delay(self, attempt: int) -> float:
        """Calculate the backoff delay for the given 0-indexed attempt number."""
        factor = self.backoff_factor**attempt
        base_delay = min(self.max_delay_sec, self.initial_delay_sec * factor)
        if not self.jitter:
            return base_delay
        # Uniform jitter between 50% and 100% of base_delay
        return random.uniform(base_delay * 0.5, base_delay)


DEFAULT_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ProviderUnavailableError,
    ProviderTimeoutError,
    TimeoutError,
    ConnectionError,
)

DEFAULT_NON_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ProviderQuotaExceededError,
    ProviderDataFormatError,
    ProviderSchemaError,
    ValueError,
    TypeError,
)


def execute_with_retry[T](
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    retryable_exceptions: Sequence[type[Exception]] = DEFAULT_RETRYABLE_EXCEPTIONS,
    non_retryable_exceptions: Sequence[type[Exception]] = DEFAULT_NON_RETRYABLE_EXCEPTIONS,
    op_name: str = "market_data_operation",
) -> T:
    """Execute a provider operation with controlled retry on transient faults.

    Args:
        operation: Callable executing provider interaction.
        policy: Configured RetryPolicy (defaults to 3 retries, exponential backoff).
        sleeper: Delay execution function (defaults to time.sleep, customizable for tests).
        retryable_exceptions: Exceptions that trigger exponential backoff.
        non_retryable_exceptions: Exceptions that fail-fast immediately without retry.
        op_name: Descriptive operation label for logging and error reporting.

    Returns:
        The result of the operation if successful.

    Raises:
        ProviderQuotaExceededError: Immediately on quota exhaustion (fail-fast).
        ProviderSchemaError: Immediately on schema mismatch (fail-fast).
        ProviderDataFormatError: Immediately on corrupted data (fail-fast).
        ProviderUnavailableError: When all retries are exhausted on transient errors.
    """
    effective_policy = policy or RetryPolicy()
    retries = effective_policy.max_retries
    retryable_tuple = tuple(retryable_exceptions)
    non_retryable_tuple = tuple(non_retryable_exceptions)

    attempt = 0
    last_exception: Exception | None = None

    while attempt <= retries:
        try:
            return operation()
        except non_retryable_tuple as exc:
            # Non-retryable: fail-fast immediately without wasting quota or time
            logger.error(
                "Non-retryable provider error during '%s': %s (type=%s)",
                op_name,
                exc,
                type(exc).__name__,
            )
            raise
        except retryable_tuple as exc:
            last_exception = exc
            if attempt >= retries:
                logger.error(
                    "Exhausted all %d retries for '%s'. Last error: %s",
                    retries,
                    op_name,
                    exc,
                )
                break

            delay = effective_policy.calculate_delay(attempt)
            logger.warning(
                "Transient error in '%s' (attempt %d/%d): %s. Backing off for %.2fs",
                op_name,
                attempt + 1,
                retries,
                exc,
                delay,
            )
            sleeper(delay)
            attempt += 1
        except Exception as exc:
            # Unrecognized exception: fail fast to avoid unpredictable behavior
            logger.exception("Unexpected error during '%s': %s", op_name, exc)
            raise ProviderError(f"Unexpected provider error during '{op_name}': {exc}") from exc

    if last_exception is not None:
        if isinstance(last_exception, ProviderError):
            raise last_exception
        raise ProviderUnavailableError(
            f"Operation '{op_name}' failed after {retries} retries: {last_exception}"
        ) from last_exception

    raise ProviderUnavailableError(f"Operation '{op_name}' failed unexpectedly with no result")


class ResilientMarketDataProvider(BaseMarketDataProvider):
    """Adapter wrapper adding retry, latency tracking, and fail-fast resilience.

    Decorates an underlying BaseMarketDataProvider.
    """

    def __init__(
        self,
        inner_provider: BaseMarketDataProvider,
        policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner_provider = inner_provider
        self.policy = policy or RetryPolicy()
        self.sleeper = sleeper

        # Telemetry counters
        self.total_invocations: int = 0
        self.retry_invocations: int = 0
        self.failed_invocations: int = 0
        self.last_latency_ms: float | None = None

    @property
    def name(self) -> str:
        return f"resilient({self.inner_provider.name})"

    @property
    def is_licensed(self) -> bool:
        return self.inner_provider.is_licensed

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        """Fetch daily bars with bounded retry on transient outages."""
        self.total_invocations += 1
        t0 = time.perf_counter()
        try:
            result = execute_with_retry(
                lambda: self.inner_provider.fetch_daily_bars(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                ),
                policy=self.policy,
                sleeper=self.sleeper,
                op_name=f"fetch_daily_bars({symbol})",
            )
            return result
        except Exception:
            self.failed_invocations += 1
            raise
        finally:
            self.last_latency_ms = (time.perf_counter() - t0) * 1000.0

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        """Fetch latest bar with bounded retry on transient outages."""
        self.total_invocations += 1
        t0 = time.perf_counter()
        try:
            return execute_with_retry(
                lambda: self.inner_provider.fetch_latest_bar(symbol=symbol),
                policy=self.policy,
                sleeper=self.sleeper,
                op_name=f"fetch_latest_bar({symbol})",
            )
        except Exception:
            self.failed_invocations += 1
            raise
        finally:
            self.last_latency_ms = (time.perf_counter() - t0) * 1000.0

    def get_supported_symbols(self) -> list[str]:
        """Retrieve supported symbols with retry."""
        self.total_invocations += 1
        try:
            return execute_with_retry(
                self.inner_provider.get_supported_symbols,
                policy=self.policy,
                sleeper=self.sleeper,
                op_name="get_supported_symbols",
            )
        except Exception:
            self.failed_invocations += 1
            raise

    def health_check(self) -> ProviderHealth:
        """Execute liveness check and return provider health with measured latency."""
        t0 = time.perf_counter()
        try:
            inner_health = self.inner_provider.health_check()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.last_latency_ms = elapsed_ms

            # Return wrapped health with updated latency
            return ProviderHealth(
                is_healthy=inner_health.is_healthy,
                provider_name=self.name,
                is_licensed=inner_health.is_licensed,
                latency_ms=elapsed_ms,
                quota_remaining=inner_health.quota_remaining,
                quota_limit=inner_health.quota_limit,
                message=inner_health.message,
            )
        except ProviderQuotaExceededError as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ProviderHealth(
                is_healthy=False,
                provider_name=self.name,
                is_licensed=self.is_licensed,
                latency_ms=elapsed_ms,
                quota_remaining=0,
                quota_limit=exc.quota_limit,
                message=f"Quota exceeded: {exc}",
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ProviderHealth(
                is_healthy=False,
                provider_name=self.name,
                is_licensed=self.is_licensed,
                latency_ms=elapsed_ms,
                quota_remaining=None,
                quota_limit=None,
                message=f"Health check failed: {exc}",
            )
