"""Test suite for operational monitoring.

Tests data freshness, API error rates, p95/p99 SLA, queue latency, and email delivery.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stock_analysis.alerts.email_notifier import AlertNotificationPayload
from stock_analysis.alerts.queue import AlertDispatchQueue
from stock_analysis.api.app import create_app
from stock_analysis.observability import (
    DATA_FRESHNESS_SECONDS,
    DATA_PROVIDER_HEALTH,
    EMAIL_DELIVERIES_TOTAL,
    EMAIL_DELIVERY_DURATION_SECONDS,
    HTTP_ERRORS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    OPERATIONAL_MONITOR,
    QUEUE_LATENCY_SECONDS,
    QUEUE_PENDING_TASKS,
    QUEUE_PROCESSED_TASKS_TOTAL,
    DataFreshnessStatus,
    EmailDeliveryStatus,
    OperationalMonitor,
)


@pytest.fixture(autouse=True)
def clean_telemetry() -> None:
    """Reset monitor and metrics before each test run."""
    OPERATIONAL_MONITOR.reset()
    HTTP_REQUESTS_TOTAL._values.clear()
    HTTP_ERRORS_TOTAL._values.clear()
    HTTP_REQUEST_DURATION_SECONDS._samples.clear()
    HTTP_REQUEST_DURATION_SECONDS._counts.clear()
    HTTP_REQUEST_DURATION_SECONDS._sums.clear()
    DATA_FRESHNESS_SECONDS._values.clear()
    DATA_PROVIDER_HEALTH._values.clear()
    QUEUE_LATENCY_SECONDS._samples.clear()
    QUEUE_LATENCY_SECONDS._counts.clear()
    QUEUE_LATENCY_SECONDS._sums.clear()
    QUEUE_PENDING_TASKS._values.clear()
    QUEUE_PROCESSED_TASKS_TOTAL._values.clear()
    EMAIL_DELIVERIES_TOTAL._values.clear()
    EMAIL_DELIVERY_DURATION_SECONDS._samples.clear()
    EMAIL_DELIVERY_DURATION_SECONDS._counts.clear()
    EMAIL_DELIVERY_DURATION_SECONDS._sums.clear()


class TestDataFreshnessMonitoring:
    """Tests for market data age evaluation and provider availability."""

    def test_fresh_market_data(self) -> None:
        now = datetime(2026, 10, 28, 14, 0, 0, tzinfo=UTC)
        bar_time = now - timedelta(seconds=45)

        record = OPERATIONAL_MONITOR.record_data_freshness(
            symbol="THYAO",
            provider="bist_direct",
            latest_bar_utc=bar_time,
            is_market_open=True,
            now_utc=now,
        )

        assert record.symbol == "THYAO"
        assert record.status == DataFreshnessStatus.FRESH
        assert record.is_stale is False
        assert record.lag_seconds == 45.0
        assert (
            DATA_FRESHNESS_SECONDS.get(labels={"symbol": "THYAO", "provider": "bist_direct"})
            == 45.0
        )
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "bist_direct"}) == 1.0

    def test_delayed_15m_licensed_data(self) -> None:
        now = datetime(2026, 10, 28, 14, 0, 0, tzinfo=UTC)
        bar_time = now - timedelta(minutes=16)

        record = OPERATIONAL_MONITOR.record_data_freshness(
            symbol="GARAN",
            provider="matriks",
            latest_bar_utc=bar_time,
            is_market_open=True,
            now_utc=now,
        )

        assert record.status == DataFreshnessStatus.DELAYED_15M
        assert record.is_stale is False
        assert record.lag_seconds == 960.0
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "matriks"}) == 1.0

    def test_stale_data_triggers_alert_flag(self) -> None:
        now = datetime(2026, 10, 28, 14, 0, 0, tzinfo=UTC)
        bar_time = now - timedelta(minutes=35)  # 2100 seconds lag

        record = OPERATIONAL_MONITOR.record_data_freshness(
            symbol="ASELS",
            provider="finnet",
            latest_bar_utc=bar_time,
            is_market_open=True,
            now_utc=now,
        )

        assert record.status == DataFreshnessStatus.STALE
        assert record.is_stale is True
        assert record.lag_seconds == 2100.0
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "finnet"}) == 0.0

    def test_market_closed_behavior(self) -> None:
        now = datetime(2026, 10, 28, 22, 0, 0, tzinfo=UTC)
        bar_time = now - timedelta(hours=4)

        record = OPERATIONAL_MONITOR.record_data_freshness(
            symbol="EREGL",
            provider="bist_direct",
            latest_bar_utc=bar_time,
            is_market_open=False,
            now_utc=now,
        )

        assert record.status == DataFreshnessStatus.MARKET_CLOSED
        assert record.is_stale is False
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "bist_direct"}) == 1.0


class TestApiErrorRateAndLatency:
    """Tests for HTTP 5xx error rate and p50/p90/p95/p99 SLA calculations."""

    def test_api_error_rate_calculation(self) -> None:
        assert OPERATIONAL_MONITOR.get_api_error_rate() == 0.0

        # Simulate 90 successful requests and 10 server errors
        for _ in range(90):
            HTTP_REQUESTS_TOTAL.inc(
                labels={"method": "GET", "path": "/api/v1/market/bars", "status": "200"}
            )
        for _ in range(10):
            HTTP_REQUESTS_TOTAL.inc(
                labels={"method": "GET", "path": "/api/v1/market/bars", "status": "500"}
            )
            HTTP_ERRORS_TOTAL.inc(labels={"method": "GET", "path": "/api/v1/market/bars"})

        error_rate = OPERATIONAL_MONITOR.get_api_error_rate()
        assert error_rate == pytest.approx(0.10, rel=1e-3)

        # Path-specific check
        path_error_rate = OPERATIONAL_MONITOR.get_api_error_rate(endpoint="/api/v1/market/bars")
        assert path_error_rate == pytest.approx(0.10, rel=1e-3)

    def test_api_latency_percentiles_and_sla(self) -> None:
        # Observe sample response times (0.020s to 0.450s)
        latencies = [0.020, 0.035, 0.050, 0.075, 0.120, 0.180, 0.220, 0.280, 0.350, 0.450]
        for lat in latencies:
            HTTP_REQUEST_DURATION_SECONDS.observe(
                lat, labels={"method": "GET", "path": "/api/v1/analytics/indicators"}
            )

        percentiles = OPERATIONAL_MONITOR.get_api_latency_percentiles()
        assert percentiles["p50_ms"] > 0
        assert percentiles["p95_ms"] > percentiles["p50_ms"]
        assert percentiles["p99_ms"] >= percentiles["p95_ms"]

        # Check with report SLA verification
        report = OPERATIONAL_MONITOR.get_operational_report(p95_sla_seconds=0.300)
        assert report["api"]["latencies_ms"]["p95_ms"] > 0
        assert "p95_sla_breached" in report["api"]


class TestQueueTelemetryAndAlertIntegration:
    """Tests for queue wait latency, pending queue depth, and AlertDispatchQueue instrumentation."""

    def test_direct_queue_recording(self) -> None:
        monitor = OperationalMonitor()
        monitor.record_queue_job(
            "background_tasks", wait_duration_seconds=0.085, queue_depth=12, status="completed"
        )
        monitor.record_queue_job(
            "background_tasks", wait_duration_seconds=0.140, queue_depth=11, status="completed"
        )

        summary = monitor.get_queue_summary("background_tasks")
        assert summary["background_tasks"]["pending_depth"] == 11
        assert summary["background_tasks"]["processed_count"] == 2
        assert summary["background_tasks"]["p95_wait_seconds"] > 0

    def test_alert_dispatch_queue_instruments_operational_monitor(self) -> None:
        queue = AlertDispatchQueue()
        now = datetime(2026, 10, 28, 15, 0, 0, tzinfo=UTC)

        payload = AlertNotificationPayload(
            alert_id="alt-12345",
            symbol="SISE",
            rule_name="MA50_CROSS",
            condition_summary="Fiyat 50 gunluk ortalamayi kesti",
            data_timestamp_utc=now,
            data_source="bist_direct",
            recipient_email="user@example.com",
        )

        # Enqueue: updates pending tasks gauge
        job, enqueued = queue.enqueue(payload, now=now)
        assert enqueued is True
        assert QUEUE_PENDING_TASKS.get(labels={"queue": "alert_dispatch"}) == 1.0

        # Simulate 1.5 seconds wait latency before dispatching
        dispatch_time = now + timedelta(seconds=1.5)
        dispatched_job = queue.process_next(lambda j: True, now=dispatch_time)

        assert dispatched_job is not None
        assert QUEUE_PENDING_TASKS.get(labels={"queue": "alert_dispatch"}) == 0.0
        assert (
            QUEUE_PROCESSED_TASKS_TOTAL.get(
                labels={"queue": "alert_dispatch", "status": "completed"}
            )
            == 1.0
        )
        assert QUEUE_LATENCY_SECONDS.get_percentile(95.0, labels={"queue": "alert_dispatch"}) >= 1.5


class TestEmailDeliveryMonitoring:
    """Tests for outbound email delivery success rates, failure tracking,
    and transmission latencies.
    """

    def test_record_email_delivery_and_summary(self) -> None:
        OPERATIONAL_MONITOR.record_email_delivery(
            "smtp_sendgrid", EmailDeliveryStatus.DELIVERED, 0.450
        )
        OPERATIONAL_MONITOR.record_email_delivery(
            "smtp_sendgrid", EmailDeliveryStatus.DELIVERED, 0.320
        )
        OPERATIONAL_MONITOR.record_email_delivery(
            "smtp_sendgrid", EmailDeliveryStatus.FAILED, 2.100
        )
        OPERATIONAL_MONITOR.record_email_delivery(
            "smtp_postmark", EmailDeliveryStatus.BOUNCED, 1.200
        )

        summary = OPERATIONAL_MONITOR.get_email_delivery_summary()
        assert summary["total_attempts"] == 4
        assert summary["delivered"] == 2
        assert summary["failed"] == 2  # failed + bounced
        assert summary["success_rate"] == 0.50
        assert summary["p95_duration_seconds"] > 0


class TestSystemOperationalEndpoints:
    """Tests for FastAPI /operational-health and Prometheus /metrics exposition."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_operational_health_endpoint(self, client: TestClient) -> None:
        # Seed some telemetry
        now = datetime.now(UTC)
        OPERATIONAL_MONITOR.record_data_freshness(
            symbol="KCHOL",
            provider="bist_direct",
            latest_bar_utc=now - timedelta(seconds=30),
            is_market_open=True,
        )

        response = client.get("/api/v1/operational-health")
        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "api" in data
        assert "data_freshness" in data
        assert "queues" in data
        assert "email_delivery" in data
        assert data["data_freshness"]["tracked_symbols_count"] >= 1
        assert any(item["symbol"] == "KCHOL" for item in data["data_freshness"]["items"])

    def test_prometheus_metrics_contains_operational_metrics(self, client: TestClient) -> None:
        OPERATIONAL_MONITOR.record_data_freshness(
            symbol="TUPRS",
            provider="bist_direct",
            latest_bar_utc=datetime.now(UTC) - timedelta(seconds=20),
            is_market_open=True,
        )
        OPERATIONAL_MONITOR.record_email_delivery("mailgun", "delivered", 0.300)

        response = client.get("/api/v1/metrics")
        assert response.status_code == 200

        text = response.text

        assert "data_freshness_seconds" in text
        assert "data_provider_health" in text
        assert "queue_latency_seconds" in text
        assert "queue_pending_tasks" in text
        assert "email_deliveries_total" in text
        assert "email_delivery_duration_seconds" in text
