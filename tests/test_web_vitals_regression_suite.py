"""Tests for Web Vitals telemetry ingestion and API p95 latency regression alerting."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.web_vitals_regression import (
    CallbackPerformanceAlertHandler,
    LogPerformanceAlertHandler,
    PerformanceAlertEvent,
    PerformanceAlertSeverity,
    PerformanceAlertType,
    PerformanceRegressionConfig,
    PerformanceRegressionCoordinator,
    WebVitalName,
    WebVitalRating,
    calculate_percentile,
    classify_web_vital,
    sanitize_route,
)


@pytest.fixture
def coordinator() -> PerformanceRegressionCoordinator:
    """Create an isolated PerformanceRegressionCoordinator instance for testing."""
    config = PerformanceRegressionConfig(
        window_size=100,
        min_samples_for_regression=5,
        p95_regression_threshold_pct=30.0,
        p95_min_delta_ms=30.0,
        default_p95_baseline_ms=100.0,
        default_p95_sla_ms=250.0,
        endpoint_baselines={"/api/v1/market": 80.0, "/api/v1/forecasts": 150.0},
        endpoint_slas={"/api/v1/market": 200.0, "/api/v1/forecasts": 400.0},
        web_vitals_poor_ratio_alarm_threshold=0.20,
        alert_cooldown_seconds=60.0,
    )
    coord = PerformanceRegressionCoordinator(config=config)
    return coord


@pytest.fixture
def client() -> TestClient:
    """Create FastAPI test client."""
    return TestClient(create_app())


class TestWebVitalClassificationAndSanitization:
    """Tests for Web Vital threshold categorization, route cleanup, and percentiles."""

    def test_classify_all_standard_web_vitals(self) -> None:
        """Verify rating boundaries for LCP, INP, CLS, FCP, TTFB."""
        # LCP: good <= 2500, needs_improvement <= 4000, poor > 4000
        assert classify_web_vital(WebVitalName.LCP, 1800.0) == WebVitalRating.GOOD
        assert classify_web_vital(WebVitalName.LCP, 3200.0) == WebVitalRating.NEEDS_IMPROVEMENT
        assert classify_web_vital(WebVitalName.LCP, 4500.0) == WebVitalRating.POOR

        # INP: good <= 200, needs_improvement <= 500, poor > 500
        assert classify_web_vital(WebVitalName.INP, 120.0) == WebVitalRating.GOOD
        assert classify_web_vital(WebVitalName.INP, 350.0) == WebVitalRating.NEEDS_IMPROVEMENT
        assert classify_web_vital(WebVitalName.INP, 600.0) == WebVitalRating.POOR

        # CLS: good <= 0.1, needs_improvement <= 0.25, poor > 0.25
        assert classify_web_vital(WebVitalName.CLS, 0.05) == WebVitalRating.GOOD
        assert classify_web_vital(WebVitalName.CLS, 0.18) == WebVitalRating.NEEDS_IMPROVEMENT
        assert classify_web_vital(WebVitalName.CLS, 0.40) == WebVitalRating.POOR

        # FCP: good <= 1800, needs_improvement <= 3000, poor > 3000
        assert classify_web_vital(WebVitalName.FCP, 1500.0) == WebVitalRating.GOOD
        assert classify_web_vital(WebVitalName.FCP, 2200.0) == WebVitalRating.NEEDS_IMPROVEMENT
        assert classify_web_vital(WebVitalName.FCP, 3200.0) == WebVitalRating.POOR

        # TTFB: good <= 800, needs_improvement <= 1800, poor > 1800
        assert classify_web_vital(WebVitalName.TTFB, 400.0) == WebVitalRating.GOOD
        assert classify_web_vital(WebVitalName.TTFB, 1200.0) == WebVitalRating.NEEDS_IMPROVEMENT
        assert classify_web_vital(WebVitalName.TTFB, 2100.0) == WebVitalRating.POOR

    def test_sanitize_route_variations(self) -> None:
        """Verify query strings, hashes, IDs, and long URLs are properly sanitized."""
        assert sanitize_route(None) == "/"
        assert sanitize_route("") == "/"
        assert sanitize_route("dashboard") == "/dashboard"
        assert sanitize_route("/stocks/THYAO?tab=chart#top") == "/stocks/THYAO"
        assert (
            sanitize_route("/users/123e4567-e89b-12d3-a456-426614174000/settings")
            == "/users/:id/settings"
        )
        assert sanitize_route("/items/42/details") == "/items/:id/details"

    def test_calculate_percentile_precision(self) -> None:
        """Verify linear interpolation percentile calculations."""
        assert calculate_percentile([], 95.0) == 0.0
        assert calculate_percentile([100.0], 95.0) == 100.0

        # Uniform sequence [10, 20, 30, 40, 50]
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert calculate_percentile(data, 50.0) == 30.0
        assert calculate_percentile(data, 0.0) == 10.0
        assert calculate_percentile(data, 100.0) == 50.0
        assert calculate_percentile(data, 75.0) == 40.0


class TestWebVitalIngestionAndRegression:
    """Tests for Web Vitals sample recording, summaries, and regression alerts."""

    def test_record_web_vital_updates_metrics(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify counter and gauge update upon recording a Web Vital sample."""
        sample = coordinator.record_web_vital(
            metric_name="LCP",
            value=1950.0,
            route="/dashboard",
            navigation_type="navigate",
        )
        assert sample.metric_name == WebVitalName.LCP
        assert sample.value == 1950.0
        assert sample.rating == WebVitalRating.GOOD
        assert sample.route == "/dashboard"

        summaries = coordinator.get_web_vitals_summary()
        assert len(summaries) == 1
        s = summaries[0]
        assert s.metric_name == "LCP"
        assert s.route == "/dashboard"
        assert s.sample_count == 1
        assert s.p75_value == 1950.0
        assert s.poor_ratio == 0.0
        assert s.status == "healthy"

    def test_record_web_vital_rejects_negative_and_invalid(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify negative values and unknown metric names raise ValueError."""
        with pytest.raises(ValueError, match="must be non-negative"):
            coordinator.record_web_vital("LCP", -10.0)

        with pytest.raises(ValueError, match="Unsupported Web Vital"):
            coordinator.record_web_vital("UNKNOWN_METRIC", 100.0)

    def test_web_vital_regression_triggers_alert(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify alert triggers when poor ratio or p75 exceeds threshold after min_samples."""
        callback_mock = MagicMock()
        coordinator.add_handler(CallbackPerformanceAlertHandler(callback_mock))

        # Record 5 poor LCP samples (4500ms > 4000ms poor threshold)
        for _ in range(5):
            coordinator.record_web_vital("LCP", 4600.0, route="/heavy-view")

        alerts = coordinator.get_recent_alerts()
        assert len(alerts) >= 1
        wv_alert = alerts[-1]
        assert wv_alert.alert_type == PerformanceAlertType.WEB_VITAL_REGRESSION
        assert wv_alert.severity == PerformanceAlertSeverity.WARNING
        assert wv_alert.target == "LCP@/heavy-view"
        assert wv_alert.current_value >= 4000.0
        callback_mock.assert_called()

        # Cooldown prevents spamming immediate duplicate alert
        coordinator.record_web_vital("LCP", 4700.0, route="/heavy-view")
        assert len(coordinator.get_recent_alerts()) == 1

    def test_web_vital_recovery_alert(self, coordinator: PerformanceRegressionCoordinator) -> None:
        """Verify recovery alert triggers when previously regressed metric returns to healthy."""
        # 1. Trigger regression
        for _ in range(6):
            coordinator.record_web_vital("CLS", 0.35, route="/layout")

        assert len(coordinator.get_recent_alerts()) == 1
        assert (
            coordinator.get_recent_alerts()[-1].alert_type
            == PerformanceAlertType.WEB_VITAL_REGRESSION
        )

        # 2. Add healthy samples until poor ratio drops and p75 <= good threshold
        for _ in range(40):
            coordinator.record_web_vital("CLS", 0.02, route="/layout")

        alerts = coordinator.get_recent_alerts()
        recovery_alerts = [
            a for a in alerts if a.alert_type == PerformanceAlertType.WEB_VITAL_RECOVERED
        ]
        assert len(recovery_alerts) >= 1
        assert recovery_alerts[-1].severity == PerformanceAlertSeverity.INFO


class TestApiLatencyP95Regression:
    """Tests for API p95 regression detection, SLA breaches, and recovery."""

    def test_normal_latency_stays_healthy(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify p95 within baseline does not trigger alerts and marks status normal."""
        # Baseline for /api/v1/market is 80ms
        for _ in range(10):
            coordinator.record_api_latency("/api/v1/market", 65.0)

        summary = coordinator.get_endpoint_latency("/api/v1/market")
        assert summary is not None
        assert summary.sample_count == 10
        assert summary.p50_ms == 65.0
        assert summary.p95_ms == 65.0
        assert not summary.is_regressed
        assert summary.status == "normal"
        assert len(coordinator.get_recent_alerts()) == 0

    def test_latency_regression_spike_triggers_warning(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify latency exceeding baseline by +30% triggers WARNING regression alert."""
        # Baseline is 80.0ms, SLA is 200.0ms
        # 125.0ms is +56% above baseline (delta 45ms >= min_delta 30ms) but under SLA 200ms
        for _ in range(10):
            coordinator.record_api_latency("/api/v1/market", 125.0)

        summary = coordinator.get_endpoint_latency("/api/v1/market")
        assert summary is not None
        assert summary.is_regressed
        assert summary.status == "warning"
        assert summary.regression_ratio > 1.30

        alerts = coordinator.get_recent_alerts()
        assert len(alerts) >= 1
        spike_alert = alerts[-1]
        assert spike_alert.alert_type == PerformanceAlertType.P95_REGRESSION_SPIKE
        assert spike_alert.severity == PerformanceAlertSeverity.WARNING
        assert spike_alert.target == "/api/v1/market"

    def test_latency_hard_sla_breach_triggers_critical(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify latency exceeding max SLA triggers CRITICAL alert."""
        # SLA is 200.0ms
        for _ in range(10):
            coordinator.record_api_latency("/api/v1/market", 260.0)

        summary = coordinator.get_endpoint_latency("/api/v1/market")
        assert summary is not None
        assert summary.status == "critical"
        assert summary.p95_ms > summary.max_sla_p95_ms

        alerts = coordinator.get_recent_alerts()
        assert len(alerts) >= 1
        sla_alert = alerts[-1]
        assert sla_alert.alert_type == PerformanceAlertType.P95_SLA_BREACH
        assert sla_alert.severity == PerformanceAlertSeverity.CRITICAL

    def test_latency_recovery_triggers_info(
        self, coordinator: PerformanceRegressionCoordinator
    ) -> None:
        """Verify recovery back to baseline triggers info alert."""
        # 1. Trigger regression
        for _ in range(8):
            coordinator.record_api_latency("/api/v1/market", 140.0)

        assert len(coordinator.get_recent_alerts()) == 1

        # 2. Push healthy measurements until p95 drops back within 10% of baseline (80ms * 1.1)
        for _ in range(100):
            coordinator.record_api_latency("/api/v1/market", 50.0)

        alerts = coordinator.get_recent_alerts()
        recov_alerts = [a for a in alerts if a.alert_type == PerformanceAlertType.P95_RECOVERED]
        assert len(recov_alerts) >= 1
        assert recov_alerts[-1].severity == PerformanceAlertSeverity.INFO


class TestApiEndpointsAndIntegration:
    """Tests for FastAPI HTTP routes, HTML dashboard, and middleware hook."""

    def test_ingest_web_vitals_endpoint(self, client: TestClient) -> None:
        """Test POST /api/v1/telemetry/web-vitals."""
        payload = {
            "metrics": [
                {
                    "name": "LCP",
                    "value": 2100.0,
                    "route": "/market",
                    "navigation_type": "navigate",
                },
                {
                    "name": "CLS",
                    "value": 0.04,
                    "route": "/market",
                    "navigation_type": "navigate",
                },
            ]
        }
        res = client.post("/api/v1/telemetry/web-vitals", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "recorded"
        assert data["ingested_count"] == 2

    def test_get_web_vitals_summary_endpoint(self, client: TestClient) -> None:
        """Test GET /api/v1/telemetry/web-vitals/summary."""
        res = client.get("/api/v1/telemetry/web-vitals/summary")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_performance_p95_endpoint(self, client: TestClient) -> None:
        """Test GET /api/v1/telemetry/performance/p95."""
        res = client.get("/api/v1/telemetry/performance/p95")
        assert res.status_code == 200
        items = res.json()
        assert isinstance(items, list)
        assert any(i["endpoint_group"] == "/api/v1/market" for i in items)

    def test_get_performance_alerts_endpoint(self, client: TestClient) -> None:
        """Test GET /api/v1/telemetry/performance/alerts."""
        res = client.get("/api/v1/telemetry/performance/alerts")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_get_performance_html_dashboard(self, client: TestClient) -> None:
        """Test GET /api/v1/telemetry/performance/html returns rich HTML page."""
        res = client.get("/api/v1/telemetry/performance/html")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "Web Vitals &amp; API p95" in res.text or "Web Vitals & API p95" in res.text
        assert "Uç Nokta Grubu" in res.text

    def test_observability_middleware_records_latency(self, client: TestClient) -> None:
        """Verify API traffic flows through middleware and records into coordinator."""
        res = client.get("/api/v1/health")
        assert res.status_code == 200

        # Verify summary includes /api/v1/health or /health
        lat_res = client.get("/api/v1/telemetry/performance/p95")
        assert lat_res.status_code == 200
        groups = {item["endpoint_group"] for item in lat_res.json()}
        assert any("health" in g or "api" in g for g in groups)


class TestAlertHandlersAndExposition:
    """Tests for Alert Handler edge cases and Prometheus text output."""

    def test_alert_event_to_dict(self) -> None:
        """Verify PerformanceAlertEvent serialization."""
        event = PerformanceAlertEvent(
            alert_id="ev-1",
            alert_type=PerformanceAlertType.P95_SLA_BREACH,
            target="/api/v1/forecasts",
            severity=PerformanceAlertSeverity.CRITICAL,
            message="SLA breached",
            current_value=450.0,
            threshold_or_baseline=300.0,
            timestamp_utc="2026-10-28T12:00:00Z",
            details={"samples": 20},
        )
        d = event.to_dict()
        assert d["alert_id"] == "ev-1"
        assert d["alert_type"] == "p95_sla_breach"
        assert d["severity"] == "critical"

    def test_log_handler_coverage(self) -> None:
        """Ensure LogPerformanceAlertHandler runs without exception across all severities."""
        handler = LogPerformanceAlertHandler()
        for sev in PerformanceAlertSeverity:
            event = PerformanceAlertEvent(
                alert_id=f"test-{sev.value}",
                alert_type=PerformanceAlertType.P95_REGRESSION_SPIKE,
                target="/test",
                severity=sev,
                message="Testing log handler",
                current_value=120.0,
                threshold_or_baseline=100.0,
                timestamp_utc="2026-10-28T12:00:00Z",
            )
            handler.handle_alert(event)

    def test_callback_handler_exception_safety(self) -> None:
        """Ensure broken callback does not crash coordinator or handler pipeline."""
        broken_callback = MagicMock(side_effect=RuntimeError("handler explosion"))
        handler = CallbackPerformanceAlertHandler(broken_callback)
        event = PerformanceAlertEvent(
            alert_id="safe-1",
            alert_type=PerformanceAlertType.P95_REGRESSION_SPIKE,
            target="/test",
            severity=PerformanceAlertSeverity.WARNING,
            message="Test error safety",
            current_value=150.0,
            threshold_or_baseline=100.0,
            timestamp_utc="2026-10-28T12:00:00Z",
        )
        # Should not raise
        handler.handle_alert(event)
        broken_callback.assert_called_once()
