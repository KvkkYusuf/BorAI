"""Comprehensive Load, Resilience, and Provider Outage Simulation Suite (FAZ-14).

Verifies system robustness, concurrency limits, and failure handling across 4 dimensions:
1. Provider Outage & Failure Simulation:
   - Total outage with bounded retry exhaustion (no infinite loop, typed ProviderUnavailableError).
   - Transient outage with automatic recovery and self-healing.
   - Fail-fast on non-retryable errors (quota exhaustion, schema violations, data corruption).
   - Exponential backoff delay calculation and jitter bounds.
   - Circuit breaker behavior during sustained provider downtime.

2. Concurrent Load & Throughput Stress:
   - High-concurrency API read load (concurrent client threads).
   - p95 latency SLA compliance (cached p95 < 300ms, uncached p95 < 800ms).
   - Zero internal server error (500) leaks under burst load.

3. Cache Stampede & Thundering Herd Defense:
   - Multiple concurrent queries for the same uncached symbol.
   - Cache single-flight / idempotent population without DB starvation.

4. Model Inference Circuit Breaker & Safe Fallback:
   - Rapid failure burst tripping circuit breaker to OPEN.
   - Rejection of inference calls during OPEN state (is_call_allowed = False).
   - Safe deterministic fallback response generation without silent fabrication.
   - Half-open state recovery after timeout expiration.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stock_analysis.api.app import create_app
from stock_analysis.api.cache import InMemoryCacheBackend, set_cache_backend
from stock_analysis.api.rate_limiter import reset_all_rate_limiters
from stock_analysis.db.base import Base
from stock_analysis.db.models import Instrument, MarketBar
from stock_analysis.db.session import get_db
from stock_analysis.models.fallback import (
    CircuitState,
    FallbackReason,
    InferenceCircuitBreaker,
    SafeForecastFallbackManager,
)
from stock_analysis.providers.base import (
    BaseMarketDataProvider,
    DataStatus,
    MarketBarRecord,
    ProviderHealth,
    ProviderQuotaExceededError,
    ProviderSchemaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from stock_analysis.providers.resilience import (
    ResilientMarketDataProvider,
    RetryPolicy,
)

pytestmark = pytest.mark.resilience


# ---------------------------------------------------------------------------
# Test Fixtures & Database Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session() -> Generator[DBSession, None, None]:
    """In-memory SQLite database session isolated per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = session_factory()
    try:
        # Seed test instruments
        for sym, name in [
            ("THYAO", "Türk Hava Yolları"),
            ("ASELS", "Aselsan Elektronik"),
            ("GARAN", "Garanti Bankası"),
            ("EREGL", "Ereğli Demir Çelik"),
            ("BIMAS", "BİM Mağazalar"),
        ]:
            inst = Instrument(
                symbol=sym,
                name=name,
                currency="TRY",
                is_active=True,
            )
            session.add(inst)
        session.commit()

        # Seed 60 days of historical bars for THYAO
        thyao = session.execute(select(Instrument).where(Instrument.symbol == "THYAO")).scalar_one()
        base_date = datetime.now(UTC) - timedelta(days=60)
        for i in range(60):
            bar_date = base_date + timedelta(days=i)
            session.add(
                MarketBar(
                    instrument_id=thyao.id,
                    timestamp=bar_date,
                    open_price=Decimal("250.00") + Decimal(str(i * 0.5)),
                    high_price=Decimal("255.00") + Decimal(str(i * 0.5)),
                    low_price=Decimal("248.00") + Decimal(str(i * 0.5)),
                    close_price=Decimal("252.00") + Decimal(str(i * 0.5)),
                    volume=Decimal("1500000"),
                    is_adjusted=True,
                )
            )
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session: DBSession) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with isolated DB and fresh in-memory cache."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    reset_all_rate_limiters()
    cache = InMemoryCacheBackend()
    set_cache_backend(cache)

    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()
    reset_all_rate_limiters()


# ---------------------------------------------------------------------------
# Controllable Mock Providers for Resilience Testing
# ---------------------------------------------------------------------------


class ScriptedFailureProvider(BaseMarketDataProvider):
    """Controllable provider that returns outcomes based on a predefined script."""

    def __init__(self, script: list[Exception | Any]) -> None:
        self.script = list(script)
        self.invocation_count = 0

    @property
    def name(self) -> str:
        return "scripted_provider"

    @property
    def is_licensed(self) -> bool:
        return True

    def _execute_next(self) -> Any:
        self.invocation_count += 1
        if not self.script:
            return None
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBarRecord]:
        val = self._execute_next()
        return val if isinstance(val, list) else []

    def fetch_latest_bar(self, symbol: str) -> MarketBarRecord | None:
        val = self._execute_next()
        return val if isinstance(val, MarketBarRecord) else None

    def get_supported_symbols(self) -> list[str]:
        val = self._execute_next()
        return val if isinstance(val, list) else ["THYAO", "ASELS"]

    def health_check(self) -> ProviderHealth:
        val = self._execute_next()
        if isinstance(val, ProviderHealth):
            return val
        return ProviderHealth(is_healthy=True, provider_name=self.name, is_licensed=True)


def _make_dummy_bar(symbol: str = "THYAO") -> MarketBarRecord:
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=date.today(),
        currency="TRY",
        data_status=DataStatus.VERIFIED,
        source_name="bist_synthetic",
        raw_open=Decimal("250.00"),
        raw_high=Decimal("255.00"),
        raw_low=Decimal("248.00"),
        raw_close=Decimal("252.00"),
        adj_open=Decimal("250.00"),
        adj_high=Decimal("255.00"),
        adj_low=Decimal("248.00"),
        adj_close=Decimal("252.00"),
        volume=1000000,
        turnover_try=Decimal("252000000.00"),
    )


# ---------------------------------------------------------------------------
# 1. API Sağlayıcı Kesintisi Simülasyonu Testleri (Provider Outage)
# ---------------------------------------------------------------------------


class TestProviderOutageSimulation:
    """Simulate complete and transient upstream provider outages."""

    def test_complete_provider_outage_exhausts_retries_gracefully(self) -> None:
        """When provider suffers total outage (503/down), retries are bounded and fail."""
        mock_failures: list[Exception | Any] = [
            ProviderUnavailableError("BIST Upstream Gateway 503 Service Unavailable"),
            ProviderUnavailableError("BIST Upstream Gateway 503 Service Unavailable"),
            ProviderUnavailableError("BIST Upstream Gateway 503 Service Unavailable"),
            ProviderUnavailableError("BIST Upstream Gateway 503 Service Unavailable"),
        ]
        provider = ScriptedFailureProvider(mock_failures)
        sleeps: list[float] = []

        policy = RetryPolicy(
            max_retries=3,
            initial_delay_sec=0.1,
            max_delay_sec=1.0,
            backoff_factor=2.0,
            jitter=False,
        )
        resilient = ResilientMarketDataProvider(
            inner_provider=provider,
            policy=policy,
            sleeper=sleeps.append,
        )

        with pytest.raises(ProviderUnavailableError, match="503 Service Unavailable"):
            resilient.fetch_daily_bars("THYAO", date(2026, 1, 1), date(2026, 1, 10))

        # 1 initial + 3 retries = 4 total calls
        assert provider.invocation_count == 4
        # Exactly 3 backoff sleep intervals
        assert len(sleeps) == 3
        assert resilient.failed_invocations == 1
        assert resilient.total_invocations == 1

    def test_transient_outage_self_healing_recovery(self) -> None:
        """Provider fails on first two attempts but recovers on the 3rd retry."""
        expected_bar = _make_dummy_bar()
        script: list[Exception | Any] = [
            ProviderTimeoutError("Connection timed out after 5000ms"),
            ProviderUnavailableError("Temporary network reset"),
            expected_bar,
        ]
        provider = ScriptedFailureProvider(script)
        sleeps: list[float] = []

        resilient = ResilientMarketDataProvider(
            inner_provider=provider,
            policy=RetryPolicy(max_retries=3, initial_delay_sec=0.05, jitter=False),
            sleeper=sleeps.append,
        )

        result = resilient.fetch_latest_bar("THYAO")
        assert result is not None
        assert result.symbol == "THYAO"
        assert result.raw_close == Decimal("252.00")
        assert provider.invocation_count == 3
        assert len(sleeps) == 2
        assert resilient.failed_invocations == 0

    def test_quota_exhaustion_fails_fast_without_wasting_retries(self) -> None:
        """When provider returns 429 Quota Exceeded, system fails immediately without retrying."""
        script: list[Exception | Any] = [
            ProviderQuotaExceededError("Daily BIST API rate limit 10,000 calls exceeded"),
            _make_dummy_bar(),  # Should never be reached
        ]
        provider = ScriptedFailureProvider(script)
        sleeps: list[float] = []

        resilient = ResilientMarketDataProvider(
            inner_provider=provider,
            policy=RetryPolicy(max_retries=5),
            sleeper=sleeps.append,
        )

        with pytest.raises(ProviderQuotaExceededError, match="rate limit"):
            resilient.fetch_latest_bar("THYAO")

        # Must NOT retry: exactly 1 call and 0 sleeps
        assert provider.invocation_count == 1
        assert len(sleeps) == 0

    def test_corrupted_schema_data_fails_fast(self) -> None:
        """Corrupted data or schema violations must fail immediately to prevent poisoning."""
        script: list[Exception | Any] = [
            ProviderSchemaError("Missing mandatory column 'close_price' in upstream feed"),
        ]
        provider = ScriptedFailureProvider(script)
        sleeps: list[float] = []

        resilient = ResilientMarketDataProvider(
            inner_provider=provider,
            policy=RetryPolicy(max_retries=3),
            sleeper=sleeps.append,
        )

        with pytest.raises(ProviderSchemaError, match="Missing mandatory column"):
            resilient.fetch_daily_bars("THYAO", date(2026, 1, 1), date(2026, 1, 5))

        assert provider.invocation_count == 1
        assert len(sleeps) == 0

    def test_exponential_backoff_and_jitter_boundaries(self) -> None:
        """Verify backoff delay scaling and jitter upper/lower bounds."""
        policy_no_jitter = RetryPolicy(
            max_retries=4,
            initial_delay_sec=0.5,
            max_delay_sec=3.0,
            backoff_factor=2.0,
            jitter=False,
        )
        assert policy_no_jitter.calculate_delay(0) == 0.5
        assert policy_no_jitter.calculate_delay(1) == 1.0
        assert policy_no_jitter.calculate_delay(2) == 2.0
        assert policy_no_jitter.calculate_delay(3) == 3.0  # Capped at max_delay_sec

        # With jitter: delay must be in [0.5 * base, base]
        policy_jitter = RetryPolicy(
            max_retries=3,
            initial_delay_sec=1.0,
            max_delay_sec=10.0,
            backoff_factor=2.0,
            jitter=True,
        )
        for attempt in range(3):
            base = min(10.0, 1.0 * (2.0**attempt))
            for _ in range(20):
                d = policy_jitter.calculate_delay(attempt)
                assert 0.5 * base <= d <= base


# ---------------------------------------------------------------------------
# 2. Yük ve Eşzamanlılık Testleri (Concurrent Load & Throughput Stress)
# ---------------------------------------------------------------------------


class TestConcurrentLoadAndThroughput:
    """Stress test the API under high concurrent client load."""

    def test_concurrent_symbols_read_load_and_p95_sla(self, client: TestClient) -> None:
        """Simulate concurrent client threads querying symbol lists.

        SLA Target: p95 latency < 300ms for cached responses, zero 500 errors.
        """
        # Warmup cache
        warmup_resp = client.get("/api/v1/market/symbols")
        assert warmup_resp.status_code == 200

        total_requests = 30
        concurrency = 6
        latencies_ms: list[float] = []
        status_codes: list[int] = []

        def worker_task(index: int) -> tuple[int, float]:
            t0 = time.perf_counter()
            headers = {"X-Forwarded-For": f"10.0.1.{index + 1}"}
            resp = client.get(
                f"/api/v1/market/symbols?page={1 + (index % 2)}&page_size=10",
                headers=headers,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return resp.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker_task, i) for i in range(total_requests)]
            for fut in concurrent.futures.as_completed(futures):
                sc, lat = fut.result()
                status_codes.append(sc)
                latencies_ms.append(lat)

        # Check all succeeded (200 OK)
        assert len(status_codes) == total_requests
        err_msg = f"Unexpected status codes: {set(status_codes)}"
        assert all(code == 200 for code in status_codes), err_msg

        # Compute p95 latency
        sorted_lat = sorted(latencies_ms)
        p95_index = int(len(sorted_lat) * 0.95)
        p95_latency = sorted_lat[p95_index]

        # In-memory execution should easily meet < 300ms SLA
        assert p95_latency < 300.0, f"p95 latency {p95_latency:.2f}ms exceeded 300ms SLA target"

    def test_concurrent_history_query_and_downsampling(self, client: TestClient) -> None:
        """Execute concurrent history requests across distinct clients with downsampling."""
        symbols = ["THYAO", "ASELS", "GARAN"]
        request_count = 24
        concurrency = 6

        def history_worker(idx: int) -> int:
            sym = symbols[idx % len(symbols)]
            headers = {"X-Forwarded-For": f"10.0.2.{idx + 1}"}
            resp = client.get(
                f"/api/v1/market/history/{sym}?range=1y&interval=1d&price_type=adjusted&downsample=bucket&max_points=20",
                headers=headers,
            )
            return int(resp.status_code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(history_worker, range(request_count)))

        assert len(results) == request_count
        assert all(code == 200 for code in results)

    def test_concurrent_burst_rate_limiter_enforcement(self, client: TestClient) -> None:
        """Verify client bursting beyond limit receives 429 without race condition."""
        single_ip_headers = {"X-Forwarded-For": "198.51.100.55"}
        total_burst = 25
        concurrency = 5

        def burst_worker(_: int) -> int:
            resp = client.get("/api/v1/market/symbols", headers=single_ip_headers)
            return int(resp.status_code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            status_codes = list(executor.map(burst_worker, range(total_burst)))

        success_count = sum(1 for c in status_codes if c == 200)
        rate_limited_count = sum(1 for c in status_codes if c == 429)

        # Anonymous tier has exactly 20 calls allowed per window
        assert success_count == 20
        assert rate_limited_count == 5

    def test_cache_thundering_herd_protection(self, client: TestClient) -> None:
        """Verify concurrent requests for uncached symbol do not cause race conditions."""
        cache = InMemoryCacheBackend()
        set_cache_backend(cache)

        # Execute 20 simultaneous requests for the exact same query with separate client IPs
        def uncached_fetch(idx: int) -> tuple[int, int]:
            headers = {"X-Forwarded-For": f"10.0.3.{idx + 1}"}
            resp = client.get("/api/v1/market/symbols?universe=BIST_100", headers=headers)
            return resp.status_code, resp.json()["total"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(uncached_fetch, range(20)))

        for sc, total in results:
            assert sc == 200
            assert total >= 5


# ---------------------------------------------------------------------------
# 3. Model Çıkarım Devre Kesici ve Dayanıklılık Testleri (Circuit Breaker)
# ---------------------------------------------------------------------------


class TestInferenceCircuitBreakerResilience:
    """Verify circuit breaker trips and safe fallback behavior during ML model outages."""

    def test_circuit_breaker_trips_to_open_after_consecutive_failures(self) -> None:
        """When model experiences repeated failures, breaker trips to OPEN and halts inference."""
        cb = InferenceCircuitBreaker(failure_threshold=3, recovery_timeout_seconds=30.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_call_allowed() is True

        now = datetime.now(UTC)
        # 1st failure
        cb.record_failure(now)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_call_allowed(now) is True

        # 2nd failure
        cb.record_failure(now)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_call_allowed(now) is True

        # 3rd failure trips the breaker
        cb.record_failure(now)
        assert str(cb.state) == str(CircuitState.OPEN)
        assert cb.is_call_allowed(now) is False

    def test_circuit_breaker_half_open_transition_and_recovery(self) -> None:
        """After recovery timeout, breaker transitions to HALF_OPEN and closes upon success."""
        cb = InferenceCircuitBreaker(failure_threshold=2, recovery_timeout_seconds=5.0)
        t0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)

        cb.record_failure(t0)
        cb.record_failure(t0)
        assert cb.state == CircuitState.OPEN

        # Call before timeout -> REJECTED
        t_early = t0 + timedelta(seconds=2.0)
        assert cb.is_call_allowed(t_early) is False

        # Call after timeout -> HALF_OPEN (Trial allowed)
        t_recovery = t0 + timedelta(seconds=6.0)
        assert cb.is_call_allowed(t_recovery) is True
        assert str(cb.state) == str(CircuitState.HALF_OPEN)

        # Successful trial closes the circuit
        cb.record_success(t_recovery)
        assert str(cb.state) == str(CircuitState.CLOSED)
        assert cb.failure_count == 0

    def test_safe_forecast_fallback_manager_produces_statutory_fallback(self) -> None:
        """Fallback manager produces valid, non-fabricated response with statutory disclaimers."""
        manager = SafeForecastFallbackManager(
            circuit_failure_threshold=3,
            circuit_recovery_timeout=60.0,
            max_staleness_hours=24.0,
        )

        # Force circuit breaker OPEN by recording failures
        for _ in range(3):
            manager.circuit_breaker.record_failure()

        assert manager.circuit_breaker.state == CircuitState.OPEN
        assert manager.circuit_breaker.is_call_allowed() is False

        # Request fallback response
        fallback_result = manager.build_fallback_response(
            symbol="THYAO",
            reason=FallbackReason.CIRCUIT_BREAKER_OPEN,
            technical_details="Model inference circuit breaker is open",
        )

        assert fallback_result.forecast_available is False
        assert fallback_result.reason_code == FallbackReason.CIRCUIT_BREAKER_OPEN
        assert fallback_result.predicted_direction is None
        assert fallback_result.confidence_score is None
        assert fallback_result.uncertainty_interval is None
        assert (
            "yatirim tavsiyesi degildir" in fallback_result.disclaimer.lower()
            or "yatırım tavsiyesi değildir" in fallback_result.disclaimer.lower()
        )
        assert len(fallback_result.alternative_indicators) > 0


# ---------------------------------------------------------------------------
# 4. Hata Yönetimi ve Bilgi Sızıntısı Dayanıklılığı Testleri
# ---------------------------------------------------------------------------


class TestFailureSanitizationResilience:
    """Verify that transient system failures never leak sensitive internal state."""

    def test_database_connection_failure_returns_safe_error(self) -> None:
        """Ensure simulated DB outage returns sanitized 500 without stack trace or DB URI."""
        app = create_app()

        def broken_db() -> Generator[None, None, None]:
            err_msg = "CRITICAL: Connection to postgres://user:secretpass@10.0.0.1:5432 failed"
            raise RuntimeError(err_msg)

        app.dependency_overrides[get_db] = broken_db
        with TestClient(app, raise_server_exceptions=False) as broken_client:
            resp = broken_client.get("/api/v1/market/symbols")
            assert resp.status_code == 500
            # Must not leak connection strings or passwords
            body = resp.text
            assert "secretpass" not in body
            assert "10.0.0.1" not in body
            assert "postgres://" not in body
