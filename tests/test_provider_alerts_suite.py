"""Comprehensive test suite for market data provider outage and quota alerting.

Tests outage debouncing, recovery transitions, quota warning and exhaustion alarms,
rate limit bursts, cooldown deduplication, alert handlers, and FastAPI endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability import (
    DATA_PROVIDER_HEALTH,
    OPERATIONAL_MONITOR,
    PROVIDER_ALERT_MANAGER,
    PROVIDER_ALERTS_TOTAL,
    PROVIDER_CONSECUTIVE_FAILURES,
    PROVIDER_OUTAGE_STATE,
    PROVIDER_QUOTA_REMAINING,
    PROVIDER_QUOTA_USAGE_RATIO,
    CallbackProviderAlertHandler,
    InMemoryAlertHistoryHandler,
    LogProviderAlertHandler,
    ProviderAlertConfig,
    ProviderAlertEvent,
    ProviderAlertManager,
    ProviderAlertSeverity,
    ProviderAlertType,
)


@pytest.fixture(autouse=True)
def clean_provider_telemetry() -> None:
    """Reset provider alerts and metrics before each test run."""
    PROVIDER_ALERT_MANAGER.reset()
    OPERATIONAL_MONITOR.reset()
    PROVIDER_OUTAGE_STATE._values.clear()
    PROVIDER_CONSECUTIVE_FAILURES._values.clear()
    PROVIDER_ALERTS_TOTAL._values.clear()
    PROVIDER_QUOTA_USAGE_RATIO._values.clear()
    PROVIDER_QUOTA_REMAINING._values.clear()
    DATA_PROVIDER_HEALTH._values.clear()


class TestProviderOutageDetection:
    """Tests for provider failure debouncing, outage triggers, and recovery lifecycle."""

    def test_transient_failure_does_not_trigger_outage(self) -> None:
        config = ProviderAlertConfig(consecutive_failure_threshold=3, recovery_success_threshold=2)
        manager = ProviderAlertManager(config=config)

        # 1st failure: below threshold
        events1 = manager.record_failure(
            "alpha_vantage", status_code=503, error_message="Service Unavailable"
        )
        assert len(events1) == 0
        status = manager.get_provider_status("alpha_vantage")
        assert status is not None
        assert status.is_outage is False
        assert status.consecutive_failures == 1
        assert manager.get_active_outages() == []

        # 2nd failure: still below threshold
        events2 = manager.record_failure(
            "alpha_vantage", status_code=504, error_message="Gateway Timeout"
        )
        assert len(events2) == 0
        status = manager.get_provider_status("alpha_vantage")
        assert status is not None
        assert status.is_outage is False
        assert status.consecutive_failures == 2

    def test_consecutive_failures_reach_threshold_triggers_outage(self) -> None:
        config = ProviderAlertConfig(consecutive_failure_threshold=3, recovery_success_threshold=2)
        manager = ProviderAlertManager(config=config)

        manager.record_failure("alpha_vantage", status_code=500)
        manager.record_failure("alpha_vantage", status_code=502)
        events = manager.record_failure(
            "alpha_vantage", status_code=503, error_message="Service Unavailable"
        )

        assert len(events) == 1
        outage_event = events[0]
        assert outage_event.alert_type == ProviderAlertType.OUTAGE
        assert outage_event.severity == ProviderAlertSeverity.CRITICAL
        assert outage_event.provider == "alpha_vantage"
        assert "is down" in outage_event.message

        status = manager.get_provider_status("alpha_vantage")
        assert status is not None
        assert status.is_outage is True
        assert status.consecutive_failures == 3
        assert "alpha_vantage" in manager.get_active_outages()

        # Gauge verified
        assert PROVIDER_OUTAGE_STATE.get(labels={"provider": "alpha_vantage"}) == 1.0
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "alpha_vantage"}) == 0.0

    def test_outage_alert_cooldown_suppresses_subsequent_duplicate_alerts(self) -> None:
        config = ProviderAlertConfig(consecutive_failure_threshold=3, cooldown_seconds=300.0)
        manager = ProviderAlertManager(config=config)
        t0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)

        # 3 failures at t0 -> Outage triggered
        manager.record_failure("bist_feed", status_code=500, now_utc=t0)
        manager.record_failure("bist_feed", status_code=500, now_utc=t0)
        events_initial = manager.record_failure("bist_feed", status_code=500, now_utc=t0)
        assert len(events_initial) == 1

        # 4th failure within cooldown (t0 + 60s) -> No duplicate OUTAGE alert
        t1 = t0 + timedelta(seconds=60)
        events_during_cooldown = manager.record_failure("bist_feed", status_code=500, now_utc=t1)
        assert len(events_during_cooldown) == 0

        # Status still records outage and incremented failure count
        status = manager.get_provider_status("bist_feed")
        assert status is not None
        assert status.is_outage is True
        assert status.consecutive_failures == 4

    def test_outage_recovery_lifecycle(self) -> None:
        config = ProviderAlertConfig(consecutive_failure_threshold=2, recovery_success_threshold=2)
        manager = ProviderAlertManager(config=config)

        # Trigger outage
        manager.record_failure("fin_data", status_code=500)
        manager.record_failure("fin_data", status_code=500)
        assert manager.get_provider_status("fin_data").is_outage is True  # type: ignore[union-attr]

        # 1st success: below recovery threshold
        rec1 = manager.record_success("fin_data")
        assert len(rec1) == 0
        assert manager.get_provider_status("fin_data").is_outage is True  # type: ignore[union-attr]

        # 2nd success: reaches recovery threshold
        rec2 = manager.record_success("fin_data")
        assert len(rec2) == 1
        recovery_event = rec2[0]
        assert recovery_event.alert_type == ProviderAlertType.RECOVERED
        assert recovery_event.severity == ProviderAlertSeverity.INFO
        assert "has recovered" in recovery_event.message

        status = manager.get_provider_status("fin_data")
        assert status is not None
        assert status.is_outage is False
        assert status.consecutive_failures == 0
        assert status.consecutive_successes == 2

        # Gauges reset
        assert PROVIDER_OUTAGE_STATE.get(labels={"provider": "fin_data"}) == 0.0
        assert DATA_PROVIDER_HEALTH.get(labels={"provider": "fin_data"}) == 1.0


class TestProviderQuotaAlerting:
    """Tests for daily quota warning and exhaustion alarms."""

    def test_normal_quota_usage_emits_no_alert(self) -> None:
        manager = ProviderAlertManager()
        events = manager.record_quota_usage("yahoo_finance", calls_made=500, daily_limit=1000)
        assert len(events) == 0
        status = manager.get_provider_status("yahoo_finance")
        assert status is not None
        assert status.quota_remaining == 500
        assert status.quota_usage_ratio == 0.50

    def test_quota_warning_triggered_at_threshold(self) -> None:
        config = ProviderAlertConfig(quota_warning_ratio=0.85)
        manager = ProviderAlertManager(config=config)

        events = manager.record_quota_usage("polygon_io", calls_made=850, daily_limit=1000)
        assert len(events) == 1
        alert = events[0]
        assert alert.alert_type == ProviderAlertType.QUOTA_WARNING
        assert alert.severity == ProviderAlertSeverity.WARNING
        assert "85.0%" in alert.message

        assert PROVIDER_QUOTA_USAGE_RATIO.get(labels={"provider": "polygon_io"}) == 0.85
        assert PROVIDER_QUOTA_REMAINING.get(labels={"provider": "polygon_io"}) == 150.0

    def test_quota_exhausted_triggered_at_limit(self) -> None:
        config = ProviderAlertConfig(quota_warning_ratio=0.85, quota_exhausted_ratio=1.0)
        manager = ProviderAlertManager(config=config)

        # Jump straight to exhausted
        events = manager.record_quota_usage("polygon_io", calls_made=1000, daily_limit=1000)
        assert len(events) == 1
        alert = events[0]
        assert alert.alert_type == ProviderAlertType.QUOTA_EXHAUSTED
        assert alert.severity == ProviderAlertSeverity.CRITICAL
        assert "exhausted" in alert.message.lower()

        assert PROVIDER_QUOTA_USAGE_RATIO.get(labels={"provider": "polygon_io"}) == 1.0
        assert PROVIDER_QUOTA_REMAINING.get(labels={"provider": "polygon_io"}) == 0.0

    def test_quota_warning_then_exhausted_sequential_alerts(self) -> None:
        config = ProviderAlertConfig(quota_warning_ratio=0.80, quota_exhausted_ratio=1.0)
        manager = ProviderAlertManager(config=config)

        # 1. 80% warning
        warn_events = manager.record_quota_usage("bist_direct", calls_made=80, daily_limit=100)
        assert len(warn_events) == 1
        assert warn_events[0].alert_type == ProviderAlertType.QUOTA_WARNING

        # 2. 85% duplicate warning suppressed
        suppressed = manager.record_quota_usage("bist_direct", calls_made=85, daily_limit=100)
        assert len(suppressed) == 0

        # 3. 100% exhaustion
        exhaust_events = manager.record_quota_usage("bist_direct", calls_made=100, daily_limit=100)
        assert len(exhaust_events) == 1
        assert exhaust_events[0].alert_type == ProviderAlertType.QUOTA_EXHAUSTED


class TestRateLimitBurst:
    """Tests for HTTP 429 rate limit burst detection."""

    def test_http_429_triggers_rate_limit_burst_alert(self) -> None:
        manager = ProviderAlertManager()
        events = manager.record_failure(
            "coingecko", status_code=429, error_message="Too Many Requests: Rate limit exceeded"
        )
        assert len(events) == 1
        burst_event = events[0]
        assert burst_event.alert_type == ProviderAlertType.RATE_LIMIT_BURST
        assert burst_event.severity == ProviderAlertSeverity.WARNING
        assert "429" in burst_event.message

    def test_http_429_cooldown_prevents_burst_spam(self) -> None:
        manager = ProviderAlertManager(config=ProviderAlertConfig(cooldown_seconds=60.0))
        t0 = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)

        events1 = manager.record_failure("coingecko", status_code=429, now_utc=t0)
        assert len(events1) == 1

        t1 = t0 + timedelta(seconds=10)
        events2 = manager.record_failure("coingecko", status_code=429, now_utc=t1)
        assert len(events2) == 0


class TestAlertHandlersAndHistory:
    """Tests for log, callback, and in-memory alert history handlers."""

    def test_callback_handler_dispatched(self) -> None:
        captured: list[ProviderAlertEvent] = []

        def on_alert(ev: ProviderAlertEvent) -> None:
            captured.append(ev)

        handler = CallbackProviderAlertHandler(on_alert)
        manager = ProviderAlertManager(handlers=[handler])

        manager.record_failure("test_feed", status_code=429)
        assert len(captured) == 1
        assert captured[0].provider == "test_feed"
        assert captured[0].alert_type == ProviderAlertType.RATE_LIMIT_BURST

    def test_log_handler_executes_safely(self) -> None:
        handler = LogProviderAlertHandler()
        event = ProviderAlertEvent(
            alert_id="alt-123",
            provider="mock_provider",
            alert_type=ProviderAlertType.OUTAGE,
            severity=ProviderAlertSeverity.CRITICAL,
            message="Outage test",
            timestamp_utc=datetime.now(UTC).isoformat(),
        )
        # Verify no unhandled exception
        handler.handle_alert(event)

    def test_in_memory_history_filtering_and_bounds(self) -> None:
        history = InMemoryAlertHistoryHandler(max_size=3)
        manager = ProviderAlertManager(handlers=[history])

        # Generate 4 events: 1st should roll off due to max_size=3
        manager.record_failure("prov_a", status_code=429)
        manager.record_failure("prov_b", status_code=429)
        manager.record_quota_usage("prov_a", calls_made=90, daily_limit=100)
        manager.record_quota_usage("prov_b", calls_made=100, daily_limit=100)

        alerts = history.get_alerts(limit=10)
        assert len(alerts) == 3

        # Filter by provider
        prov_a_alerts = history.get_alerts(provider="prov_a")
        assert all(a.provider == "prov_a" for a in prov_a_alerts)

        # Filter by alert_type
        exhausted_alerts = history.get_alerts(alert_type=ProviderAlertType.QUOTA_EXHAUSTED)
        assert len(exhausted_alerts) == 1
        assert exhausted_alerts[0].provider == "prov_b"


class TestOperationalMonitorIntegration:
    """Tests integration between OperationalMonitor and ProviderAlertManager."""

    def test_record_provider_call_outage_changes_system_status_to_critical(self) -> None:
        OPERATIONAL_MONITOR.reset()

        # Record 3 failures
        OPERATIONAL_MONITOR.record_provider_call("bist_realtime", success=False, status_code=500)
        OPERATIONAL_MONITOR.record_provider_call("bist_realtime", success=False, status_code=500)
        OPERATIONAL_MONITOR.record_provider_call("bist_realtime", success=False, status_code=503)

        report = OPERATIONAL_MONITOR.get_operational_report()
        assert report["status"] == "critical"
        assert "bist_realtime" in report["provider_health"]["active_outages"]
        assert len(report["provider_health"]["recent_alerts"]) > 0

    def test_record_provider_call_quota_tracking(self) -> None:
        OPERATIONAL_MONITOR.reset()

        OPERATIONAL_MONITOR.record_provider_call(
            "market_feed",
            success=True,
            calls_made=95,
            daily_limit=100,
        )

        report = OPERATIONAL_MONITOR.get_operational_report()
        provider_info = report["provider_health"]["providers"]["market_feed"]
        assert provider_info["calls_made"] == 95
        assert provider_info["daily_limit"] == 100
        assert provider_info["quota_remaining"] == 5
        assert provider_info["quota_usage_ratio"] == 0.95


class TestFastApiSystemEndpoints:
    """Tests for /api/v1/provider-alerts and /api/v1/operational-health HTTP endpoints."""

    def test_get_provider_alerts_endpoint(self) -> None:
        app = create_app()
        client = TestClient(app)

        # Populate telemetry
        PROVIDER_ALERT_MANAGER.record_failure("fastapi_provider", status_code=429)
        PROVIDER_ALERT_MANAGER.record_quota_usage(
            "fastapi_provider", calls_made=900, daily_limit=1000
        )

        response = client.get("/api/v1/provider-alerts")
        assert response.status_code == 200
        data = response.json()

        assert "active_outages" in data
        assert "is_healthy" in data
        assert "providers" in data
        assert "recent_alerts" in data
        assert len(data["recent_alerts"]) == 2
        assert "fastapi_provider" in data["providers"]
        assert data["providers"]["fastapi_provider"]["quota_remaining"] == 100

    def test_operational_health_includes_provider_health(self) -> None:
        app = create_app()
        client = TestClient(app)

        response = client.get("/api/v1/operational-health")
        assert response.status_code == 200
        data = response.json()

        assert "provider_health" in data
        assert "active_outages" in data["provider_health"]
        assert "providers" in data["provider_health"]
        assert "recent_alerts" in data["provider_health"]

    def test_prometheus_metrics_expose_provider_gauges(self) -> None:
        app = create_app()
        client = TestClient(app)

        PROVIDER_ALERT_MANAGER.record_quota_usage("prom_prov", calls_made=85, daily_limit=100)

        response = client.get("/api/v1/metrics")
        assert response.status_code == 200
        text = response.text

        assert "provider_quota_usage_ratio" in text
        assert "provider_quota_remaining" in text
        assert "provider_outage_state" in text
