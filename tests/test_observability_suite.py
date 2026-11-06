"""Comprehensive test suite for structured logging, metrics, and distributed tracing."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability import (
    MetricsRegistry,
    StructuredJsonFormatter,
    extract_trace_context,
    generate_span_id,
    generate_trace_id,
    get_current_span,
    inject_trace_context,
    parse_w3c_traceparent,
    trace_span,
)


class TestTracingAndContext:
    """Tests for W3C distributed trace context propagation and span life-cycle."""

    def test_generate_ids(self) -> None:
        trace_id = generate_trace_id()
        assert len(trace_id) == 32
        assert all(c in "0123456789abcdef" for c in trace_id)

        span_id = generate_span_id()
        assert len(span_id) == 16
        assert all(c in "0123456789abcdef" for c in span_id)

    def test_parse_valid_w3c_traceparent(self) -> None:
        raw = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        span = parse_w3c_traceparent(raw)
        assert span is not None
        assert span.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert span.parent_span_id == "00f067aa0ba902b7"
        assert span.sampled is True
        assert len(span.span_id) == 16
        # Generates a valid new child traceparent
        outbound = span.to_w3c_traceparent()
        assert outbound.startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
        assert outbound.endswith("-01")

    def test_parse_invalid_w3c_traceparent(self) -> None:
        # Invalid version
        assert (
            parse_w3c_traceparent("01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") is None
        )
        # All zeros trace_id
        zeros_trace = "0" * 32
        assert parse_w3c_traceparent(f"00-{zeros_trace}-00f067aa0ba902b7-01") is None
        # All zeros span_id
        zeros_span = "0" * 16
        assert parse_w3c_traceparent(f"00-4bf92f3577b34da6a3ce929d0e0e4736-{zeros_span}-01") is None
        # Malformed length or characters
        assert parse_w3c_traceparent("00-invalid-span-01") is None
        assert parse_w3c_traceparent("") is None
        assert parse_w3c_traceparent(None) is None

    def test_nested_span_context_propagation(self) -> None:
        assert get_current_span() is None

        with trace_span("parent_job", attributes={"module": "market"}) as parent:
            assert get_current_span() is parent
            assert parent.parent_span_id is None
            assert parent.attributes["span_name"] == "parent_job"

            with trace_span("child_task", attributes={"step": 2}) as child:
                assert get_current_span() is child
                assert child.trace_id == parent.trace_id
                assert child.parent_span_id == parent.span_id
                assert child.attributes["step"] == 2
                assert child.attributes["span_name"] == "child_task"

            # Restores parent on exit
            assert get_current_span() is parent

        # Cleans up completely on exit
        assert get_current_span() is None

    def test_extract_and_inject_headers(self) -> None:
        # 1. From W3C header
        headers = {"Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        extracted = extract_trace_context(headers)
        assert extracted.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert extracted.parent_span_id == "00f067aa0ba902b7"

        # 2. From X-Trace-ID header
        custom_headers = {"x-trace-id": "11223344556677889900aabbccddeeff"}
        extracted_custom = extract_trace_context(custom_headers)
        assert extracted_custom.trace_id == "11223344556677889900aabbccddeeff"

        # 3. Inbound without headers generates fresh root
        fresh = extract_trace_context({})
        assert len(fresh.trace_id) == 32
        assert fresh.parent_span_id is None

        # 4. Outbound injection
        injected = inject_trace_context(extracted)
        assert "traceparent" in injected
        assert injected["x-trace-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert injected["x-request-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


class TestStructuredLogging:
    """Tests for structured JSON log formatting, metadata correlation, and sanitization."""

    def test_structured_json_output(self) -> None:
        formatter = StructuredJsonFormatter(service_name="test_service", environment="test")
        record = logging.LogRecord(
            name="stock_analysis.test",
            level=logging.INFO,
            pathname="test_file.py",
            lineno=42,
            msg="User requested market chart for %s",
            args=("THYAO",),
            exc_info=None,
        )

        with trace_span("test_span") as span:
            output = formatter.format(record)

        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["service"] == "test_service"
        assert data["environment"] == "test"
        assert data["message"] == "User requested market chart for THYAO"
        assert data["trace_id"] == span.trace_id
        assert data["span_id"] == span.span_id
        assert data["source"]["file"] == "test_file.py"
        assert data["source"]["line"] == 42

    def test_structured_log_exception_and_extra_fields(self) -> None:
        formatter = StructuredJsonFormatter(service_name="test_service")
        try:
            raise ValueError("Test internal computation error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="stock_analysis.test",
            level=logging.ERROR,
            pathname="calc.py",
            lineno=100,
            msg="Computation failed",
            args=(),
            exc_info=exc_info,
        )
        record.__dict__["user_id"] = 999
        record.__dict__["symbol"] = "GARAN"

        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "ERROR"
        assert "error" in data
        assert data["error"]["type"] == "ValueError"
        assert "Test internal computation error" in data["error"]["message"]
        assert "extra" in data
        assert data["extra"]["symbol"] == "GARAN"
        assert data["extra"]["user_id"] == 999

    def test_sensitive_data_sanitization_in_json_log(self) -> None:
        formatter = StructuredJsonFormatter(service_name="test_service")
        record = logging.LogRecord(
            name="stock_analysis.auth",
            level=logging.WARNING,
            pathname="auth.py",
            lineno=50,
            msg=(
                "User failed login with password=SuperSecret123 and token=eyJhbGciOiJIUzI1NiJ9.test"
            ),
            args=(),
            exc_info=None,
        )
        record.__dict__["sensitive_payload"] = {
            "api_key": "AKIA1111111111111111",
            "email": "test@example.com",
        }

        output = formatter.format(record)
        assert "SuperSecret123" not in output
        assert "AKIA1111111111111111" not in output
        assert "[REDACTED]" in output


class TestMetricsRegistry:
    """Tests for in-memory thread-safe metrics, percentiles, and Prometheus exposition."""

    def test_counter_increments_and_labels(self) -> None:
        registry = MetricsRegistry()
        requests = registry.counter("http_requests", "HTTP requests count")

        requests.inc(labels={"method": "GET", "status": "200"})
        requests.inc(amount=2.0, labels={"method": "GET", "status": "200"})
        requests.inc(labels={"method": "POST", "status": "201"})

        assert requests.get(labels={"method": "GET", "status": "200"}) == 3.0
        assert requests.get(labels={"method": "POST", "status": "201"}) == 1.0
        assert requests.get(labels={"method": "GET", "status": "404"}) == 0.0

        with pytest.raises(ValueError):
            requests.inc(-1.0)

        prom_lines = requests.to_prometheus()
        assert "# TYPE http_requests counter" in prom_lines
        assert any('http_requests{method="GET",status="200"} 3.0' in line for line in prom_lines)

    def test_gauge_operations(self) -> None:
        registry = MetricsRegistry()
        active = registry.gauge("active_jobs", "Active worker jobs")

        active.set(5.0)
        assert active.get() == 5.0
        active.inc(2.0)
        assert active.get() == 7.0
        active.dec(3.0)
        assert active.get() == 4.0

        prom_lines = active.to_prometheus()
        assert "# TYPE active_jobs gauge" in prom_lines
        assert "active_jobs 4.0" in prom_lines

    def test_histogram_latency_percentiles(self) -> None:
        registry = MetricsRegistry()
        latency = registry.histogram("request_latency_seconds", "Latency histogram")

        # Record 100 observations from 0.01s to 1.00s
        for i in range(1, 101):
            latency.observe(i * 0.01, labels={"endpoint": "/quote"})

        p50 = latency.get_percentile(50.0, labels={"endpoint": "/quote"})
        p95 = latency.get_percentile(95.0, labels={"endpoint": "/quote"})
        p99 = latency.get_percentile(99.0, labels={"endpoint": "/quote"})

        assert 0.49 <= p50 <= 0.52
        assert 0.94 <= p95 <= 0.96
        assert 0.98 <= p99 <= 1.00

        prom_lines = latency.to_prometheus()
        assert "# TYPE request_latency_seconds histogram" in prom_lines
        assert any(
            'request_latency_seconds_bucket{endpoint="/quote",le="+Inf"} 100' in line
            for line in prom_lines
        )
        assert any(
            'request_latency_seconds_count{endpoint="/quote"} 100' in line for line in prom_lines
        )

    def test_metrics_registry_snapshot_and_prometheus_export(self) -> None:
        registry = MetricsRegistry()
        c = registry.counter("test_cnt", "Sample counter")
        c.inc(5.0)

        snapshot = registry.get_snapshot()
        assert "test_cnt" in snapshot["counters"]

        text = registry.generate_prometheus_text()
        assert "# HELP test_cnt Sample counter" in text
        assert "test_cnt 5.0" in text

        registry.clear()
        assert len(registry.get_snapshot()["counters"]) == 0


class TestFastApiObservabilityIntegration:
    """End-to-end integration tests for FastAPI with ObservabilityMiddleware and /metrics."""

    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app()
        return TestClient(app)

    def test_middleware_attaches_trace_headers(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        # Verify trace headers returned
        assert "traceparent" in response.headers
        assert "x-trace-id" in response.headers
        assert "x-request-id" in response.headers

        trace_id = response.headers["x-trace-id"]
        assert len(trace_id) == 32
        assert response.headers["traceparent"].startswith(f"00-{trace_id}-")

    def test_middleware_propagates_incoming_w3c_traceparent(self, client: TestClient) -> None:
        incoming_trace = "00-88888888888888888888888888888888-1111111111111111-01"
        response = client.get("/api/v1/health", headers={"traceparent": incoming_trace})
        assert response.status_code == 200

        assert response.headers["x-trace-id"] == "88888888888888888888888888888888"
        assert response.headers["traceparent"].startswith("00-88888888888888888888888888888888-")

    def test_prometheus_metrics_endpoint_integration(self, client: TestClient) -> None:
        # Trigger requests
        client.get("/api/v1/health")
        client.get("/api/v1/version")

        # Inspect metrics endpoint
        metrics_resp = client.get("/api/v1/metrics")
        assert metrics_resp.status_code == 200
        assert "text/plain" in metrics_resp.headers["content-type"]

        content = metrics_resp.text
        assert "# TYPE http_requests_total counter" in content
        assert "# TYPE http_request_duration_seconds histogram" in content
        assert "/api/v1/health" in content
