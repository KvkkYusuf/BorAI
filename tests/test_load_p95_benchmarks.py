"""Automated Load Testing & p95 Acceptance Criteria Benchmark Suite (FAZ-14).

Verifies response time SLAs defined in docs/ACCEPTANCE_CRITERIA.md:
- API (önbellekli okuma): p95 yanıt < 300 ms
- API (önbelleksiz okuma): p95 yanıt < 800 ms
- Hata Oranı: 500 İç Sunucu Hatası = 0, Kritik/yüksek açık = 0

Validates:
1. Concurrent load execution against API endpoints (cached vs uncached).
2. p95 calculation accuracy and SLA threshold comparison logic.
3. Automated pass/fail verdict generation (LoadTestVerdict).
4. Locust (locustfile.py) and k6 (k6_test_script.js) benchmark artifacts.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
from stock_analysis.testing.load_sla import (
    SLA_P95_CACHED_MAX_MS,
    SLA_P95_UNCACHED_MAX_MS,
    BorsaAnalizUser,
    calculate_percentile,
    evaluate_sla_compliance,
)

pytestmark = pytest.mark.resilience


# ---------------------------------------------------------------------------
# Fixtures: Isolated DB & API Client
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
            ("ASELS", "Aselsan"),
            ("GARAN", "Garanti Bankası"),
            ("EREGL", "Ereğli Demir Çelik"),
            ("BIMAS", "BİM Birleşik Mağazalar"),
        ]:
            inst = Instrument(
                symbol=sym,
                name=name,
                currency="TRY",
                is_active=True,
            )
            session.add(inst)
        session.commit()

        # Seed 90 days of historical bars for THYAO and ASELS
        thyao = session.execute(select(Instrument).where(Instrument.symbol == "THYAO")).scalar_one()
        asels = session.execute(select(Instrument).where(Instrument.symbol == "ASELS")).scalar_one()
        base_date = datetime.now(UTC) - timedelta(days=90)

        for i in range(90):
            bar_date = base_date + timedelta(days=i)
            session.add(
                MarketBar(
                    instrument_id=thyao.id,
                    timestamp=bar_date,
                    open_price=Decimal("250.00") + Decimal(str(i * 0.5)),
                    high_price=Decimal("255.00") + Decimal(str(i * 0.5)),
                    low_price=Decimal("248.00") + Decimal(str(i * 0.5)),
                    close_price=Decimal("252.00") + Decimal(str(i * 0.5)),
                    volume=Decimal("1200000"),
                    is_adjusted=True,
                )
            )
            session.add(
                MarketBar(
                    instrument_id=asels.id,
                    timestamp=bar_date,
                    open_price=Decimal("60.00") + Decimal(str(i * 0.2)),
                    high_price=Decimal("62.00") + Decimal(str(i * 0.2)),
                    low_price=Decimal("59.00") + Decimal(str(i * 0.2)),
                    close_price=Decimal("61.00") + Decimal(str(i * 0.2)),
                    volume=Decimal("800000"),
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


# ---------------------------------------------------------------------------
# Test Suite: p95 SLA Verification & Load Benchmarking
# ---------------------------------------------------------------------------


class TestP95AcceptanceCriteriaBenchmarks:
    """Verify concurrent load latency against ACCEPTANCE_CRITERIA.md p95 thresholds."""

    def test_cached_symbols_query_meets_p95_300ms_sla(self, client: TestClient) -> None:
        """Cached symbols queries must have p95 response time < 300ms."""
        # 1. Warm-up cache
        warmup = client.get("/api/v1/market/symbols")
        assert warmup.status_code == 200

        total_requests = 40
        concurrency = 8
        latencies_ms: list[float] = []
        status_codes: list[int] = []

        def worker(idx: int) -> tuple[int, float]:
            t0 = time.perf_counter()
            headers = {"X-Forwarded-For": f"192.168.1.{10 + idx}"}
            res = client.get("/api/v1/market/symbols?page=1&page_size=10", headers=headers)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return res.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker, i) for i in range(total_requests)]
            for fut in concurrent.futures.as_completed(futures):
                sc, lat = fut.result()
                status_codes.append(sc)
                latencies_ms.append(lat)

        assert len(status_codes) == total_requests
        assert all(sc == 200 for sc in status_codes)

        p95 = calculate_percentile(latencies_ms, 95.0)
        assert p95 < SLA_P95_CACHED_MAX_MS, (
            f"Cached symbols p95 was {p95:.2f}ms, exceeding {SLA_P95_CACHED_MAX_MS}ms target"
        )

    def test_cached_history_query_meets_p95_300ms_sla(self, client: TestClient) -> None:
        """Cached price history queries must meet p95 < 300ms under concurrent client load."""
        warmup = client.get("/api/v1/market/history/THYAO?range=1m&interval=1d&price_type=adjusted")
        assert warmup.status_code == 200

        total_requests = 30
        concurrency = 6
        latencies_ms: list[float] = []

        def history_worker(idx: int) -> tuple[int, float]:
            t0 = time.perf_counter()
            headers = {"X-Forwarded-For": f"192.168.2.{10 + idx}"}
            res = client.get(
                "/api/v1/market/history/THYAO?range=1m&interval=1d&price_type=adjusted",
                headers=headers,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return res.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(history_worker, range(total_requests)))

        assert all(sc == 200 for sc, _ in results)
        latencies_ms = [lat for _, lat in results]
        p95 = calculate_percentile(latencies_ms, 95.0)
        assert p95 < SLA_P95_CACHED_MAX_MS

    def test_uncached_history_query_meets_p95_800ms_sla(self, client: TestClient) -> None:
        """Uncached database queries with downsampling must meet p95 < 800ms."""
        total_requests = 20
        concurrency = 4
        latencies_ms: list[float] = []

        def uncached_worker(idx: int) -> tuple[int, float]:
            # Generate different query params to bypass cache
            max_pts = 10 + (idx % 15)
            t0 = time.perf_counter()
            headers = {"X-Forwarded-For": f"192.168.3.{10 + idx}"}
            res = client.get(
                f"/api/v1/market/history/ASELS?range=3m&interval=1d&downsample=bucket&max_points={max_pts}",
                headers=headers,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return res.status_code, elapsed_ms

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(uncached_worker, range(total_requests)))

        assert all(sc == 200 for sc, _ in results)
        latencies_ms = [lat for _, lat in results]
        p95 = calculate_percentile(latencies_ms, 95.0)
        assert p95 < SLA_P95_UNCACHED_MAX_MS, (
            f"Uncached history p95 was {p95:.2f}ms, exceeding {SLA_P95_UNCACHED_MAX_MS}ms target"
        )

    def test_sla_evaluator_passes_compliant_metrics(self) -> None:
        """SLA evaluator returns PASSED when all metrics satisfy acceptance criteria."""
        cached_samples = [15.0, 20.0, 25.0, 30.0, 45.0, 60.0, 110.0, 180.0, 250.0]
        uncached_samples = [80.0, 120.0, 190.0, 250.0, 320.0, 450.0, 620.0, 710.0]

        verdict = evaluate_sla_compliance(
            cached_latencies_ms=cached_samples,
            uncached_latencies_ms=uncached_samples,
            cached_errors=0,
            uncached_errors=0,
        )

        assert verdict.all_passed is True
        assert verdict.is_cached_sla_met is True
        assert verdict.is_uncached_sla_met is True
        assert verdict.is_error_rate_sla_met is True
        assert "PASSED" in verdict.summary_message

    def test_sla_evaluator_fails_on_cached_p95_exceeded(self) -> None:
        """SLA evaluator flags failure when cached p95 exceeds 300ms."""
        # 95th percentile will exceed 300ms
        cached_samples = [50.0, 100.0, 150.0, 200.0, 280.0, 320.0, 380.0, 420.0]
        uncached_samples = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]

        verdict = evaluate_sla_compliance(
            cached_latencies_ms=cached_samples,
            uncached_latencies_ms=uncached_samples,
            cached_errors=0,
            uncached_errors=0,
        )

        assert verdict.all_passed is False
        assert verdict.is_cached_sla_met is False
        assert verdict.is_uncached_sla_met is True
        assert "FAILED SLA" in verdict.summary_message
        assert "Cached p95" in verdict.summary_message

    def test_sla_evaluator_fails_on_uncached_p95_exceeded(self) -> None:
        """SLA evaluator flags failure when uncached p95 exceeds 800ms."""
        cached_samples = [20.0, 40.0, 60.0, 80.0, 120.0]
        uncached_samples = [300.0, 500.0, 700.0, 820.0, 950.0, 1100.0]

        verdict = evaluate_sla_compliance(
            cached_latencies_ms=cached_samples,
            uncached_latencies_ms=uncached_samples,
            cached_errors=0,
            uncached_errors=0,
        )

        assert verdict.all_passed is False
        assert verdict.is_cached_sla_met is True
        assert verdict.is_uncached_sla_met is False
        assert "FAILED SLA" in verdict.summary_message
        assert "Uncached p95" in verdict.summary_message

    def test_sla_evaluator_fails_on_errors(self) -> None:
        """SLA evaluator flags failure when error count > 0."""
        cached_samples = [10.0, 20.0, 30.0]
        uncached_samples = [50.0, 100.0, 150.0]

        verdict = evaluate_sla_compliance(
            cached_latencies_ms=cached_samples,
            uncached_latencies_ms=uncached_samples,
            cached_errors=1,
            uncached_errors=0,
        )

        assert verdict.all_passed is False
        assert verdict.is_error_rate_sla_met is False
        assert "Errors detected: 1" in verdict.summary_message

    def test_percentile_math_calculations(self) -> None:
        """Verify mathematical interpolation of percentiles."""
        data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]

        assert calculate_percentile([], 95.0) == 0.0
        assert calculate_percentile([42.5], 95.0) == 42.5
        assert calculate_percentile(data, 50.0) == 55.0
        assert calculate_percentile(data, 90.0) == 91.0
        assert calculate_percentile(data, 95.0) == 95.5
        assert calculate_percentile(data, 100.0) == 100.0

    def test_locustfile_and_k6_artifacts_exist_and_conform_to_criteria(self) -> None:
        """Ensure load test artifacts exist and explicitly reference ACCEPTANCE_CRITERIA.md SLAs."""
        base_dir = Path(__file__).parent.parent
        locust_file = base_dir / "tests" / "load" / "locustfile.py"
        k6_file = base_dir / "tests" / "load" / "k6_test_script.js"

        assert locust_file.exists(), f"Missing {locust_file}"
        assert k6_file.exists(), f"Missing {k6_file}"

        locust_text = locust_file.read_text(encoding="utf-8")
        assert "SLA_P95_CACHED_MAX_MS" in locust_text
        assert "SLA_P95_UNCACHED_MAX_MS" in locust_text
        assert SLA_P95_CACHED_MAX_MS == 300.0
        assert SLA_P95_UNCACHED_MAX_MS == 800.0
        assert "BorsaAnalizUser" in locust_text

        k6_text = k6_file.read_text(encoding="utf-8")
        assert "p(95)<300" in k6_text
        assert "p(95)<800" in k6_text
        assert "rate==0" in k6_text

    def test_benchmark_report_serialization(self) -> None:
        """Verify JSON serializability and structure of the benchmark report."""
        cached_samples = [12.0, 25.0, 40.0, 75.0, 120.0]
        uncached_samples = [100.0, 250.0, 450.0, 600.0, 750.0]

        verdict = evaluate_sla_compliance(
            cached_latencies_ms=cached_samples,
            uncached_latencies_ms=uncached_samples,
            cached_errors=0,
            uncached_errors=0,
        )
        report_dict = verdict.to_dict()
        json_output = json.dumps(report_dict)

        assert isinstance(json_output, str)
        assert report_dict["all_passed"] is True
        assert report_dict["overall_p95_cached_ms"] <= 300.0
        assert report_dict["overall_p95_uncached_ms"] <= 800.0
        assert len(report_dict["endpoints"]) == 2
        assert report_dict["endpoints"][0]["category"] == "cached"
        assert report_dict["endpoints"][1]["category"] == "uncached"

    def test_locust_user_class_tasks_and_weights(self) -> None:
        """Verify BorsaAnalizUser tasks, weights, and endpoints."""
        assert hasattr(BorsaAnalizUser, "query_cached_symbols")
        assert hasattr(BorsaAnalizUser, "query_cached_history")
        assert hasattr(BorsaAnalizUser, "query_uncached_history")
        assert hasattr(BorsaAnalizUser, "query_downsampled_chart")
        assert hasattr(BorsaAnalizUser, "query_health_check")
