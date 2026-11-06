"""Unit and integration test suite for Monitoring, Alarms, Incident Response
and Rollback Gate (FAZ-16).

Tests:
1. Full operational readiness gate evaluation (8/8 checks PASS).
2. Prometheus metrics telemetry completeness.
3. Alerting thresholds (P1-P4) and provider outage alert validation.
4. Incident response runbook and PICERL SLA compliance (KVKK 72h / SPK 24h).
5. SIRT 5-role directory validation.
6. Automated 5-step rollback plan generation.
7. Simulated rollback drill and RTO (< 300s) compliance.
8. Automated rollback trigger coordinator on error rate breach.
9. FastAPI REST endpoints (/pre-deployment/operations-readiness/verify and /report).
10. Simulated failure scenario handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.observability.operations_gatekeeper import (
    OPERATIONS_READINESS_GATE_STATUS,
    OPERATIONS_ROLLBACK_RTO_SECONDS,
    OperationsReadinessCategory,
    OperationsReadinessGatekeeper,
    OperationsReadinessGateReport,
    OperationsReadinessGateStatus,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture
def gatekeeper(tmp_path: Path) -> OperationsReadinessGatekeeper:
    """Isolated gatekeeper instance writing to temporary directory."""
    report_path = tmp_path / "OPERATIONS_READINESS_VERIFICATION_REPORT.md"
    return OperationsReadinessGatekeeper(report_output_path=report_path)


def test_operations_readiness_default_gate_evaluation_success(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test that pre-deployment operations readiness evaluation passes with 100% compliance."""
    report: OperationsReadinessGateReport = gatekeeper.evaluate_gate(publish_report=True)

    assert report.gate_status == OperationsReadinessGateStatus.PASS
    assert report.total_checks == 8
    assert report.passed_checks == 8
    assert report.failed_checks == 0
    assert report.rollback_rto_seconds < 300.0

    # Verify report written to disk
    assert gatekeeper.report_output_path.exists()
    content = gatekeeper.report_output_path.read_text(encoding="utf-8")
    assert "GEÇTİ (PASS)" in content
    assert "OPS-MON-01" in content
    assert "OPS-RB-02" in content

    # Verify Prometheus metrics
    assert OPERATIONS_READINESS_GATE_STATUS.get() == 1.0
    assert OPERATIONS_ROLLBACK_RTO_SECONDS.get() >= 0.0


def test_monitoring_telemetry_check(gatekeeper: OperationsReadinessGatekeeper) -> None:
    """Test Prometheus metric registry validation check."""
    check = gatekeeper.check_monitoring_telemetry()
    assert check.passed is True
    assert check.check_id == "OPS-MON-01"
    assert check.category == OperationsReadinessCategory.MONITORING_TELEMETRY
    assert len(check.violations) == 0


def test_alerting_rules_and_thresholds_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test critical alert rules and thresholds verification."""
    check = gatekeeper.check_alerting_rules_and_thresholds()
    assert check.passed is True
    assert check.check_id == "OPS-ALT-01"
    assert check.category == OperationsReadinessCategory.ALERTING_AND_THRESHOLDS
    assert "SEV-1/SEV-2" in check.details


def test_incident_response_runbook_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test presence and validity of incident response runbook."""
    check = gatekeeper.check_incident_response_runbook()
    assert check.passed is True
    assert check.check_id == "OPS-IR-01"
    assert len(check.violations) == 0


def test_incident_contact_roster_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test SIRT directory contains 5 core roles."""
    check = gatekeeper.check_incident_contact_roster()
    assert check.passed is True
    assert check.check_id == "OPS-IR-02"
    assert "5/5" in check.details


def test_statutory_incident_sla_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test KVKK 72-hour and SPK 24-hour statutory notification gate."""
    check = gatekeeper.check_statutory_incident_sla()
    assert check.passed is True
    assert check.check_id == "OPS-SLA-01"
    assert "KVKK m. 12" in check.details


def test_automated_rollback_plan_generator_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test 5-step automated rollback plan generation."""
    check = gatekeeper.check_automated_rollback_plan_generator()
    assert check.passed is True
    assert check.check_id == "OPS-RB-01"
    assert "adım" in check.details


def test_mock_rollback_drill_rto(gatekeeper: OperationsReadinessGatekeeper) -> None:
    """Test simulated sandbox rollback drill and verify RTO < 300s."""
    check, rto_seconds = gatekeeper.execute_mock_rollback_drill()
    assert check.passed is True
    assert check.check_id == "OPS-RB-02"
    assert rto_seconds < 300.0


def test_rollback_coordinator_thresholds_check(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test rollback trigger coordinator safety mechanism under error rate breach."""
    check = gatekeeper.check_rollback_coordinator_thresholds()
    assert check.passed is True
    assert check.check_id == "OPS-RB-03"


def test_operations_readiness_failure_scenario(
    gatekeeper: OperationsReadinessGatekeeper,
) -> None:
    """Test gate evaluation fails gracefully when a critical runbook is missing."""
    with patch("pathlib.Path.exists", return_value=False):
        report = gatekeeper.evaluate_gate(publish_report=False)
        assert report.gate_status == OperationsReadinessGateStatus.FAIL
        assert report.failed_checks >= 1
        assert OPERATIONS_READINESS_GATE_STATUS.get() == 0.0


def test_rest_api_operations_readiness_verify(client: TestClient) -> None:
    """Test GET /api/v1/pre-deployment/operations-readiness/verify endpoint."""
    response = client.get("/api/v1/pre-deployment/operations-readiness/verify")
    assert response.status_code == 200
    data = response.json()
    assert data["gate_status"] == "PASS"
    assert data["total_checks"] == 8
    assert data["passed_checks"] == 8
    assert data["failed_checks"] == 0
    assert data["rollback_rto_seconds"] < 300.0
    assert len(data["checks"]) == 8


def test_rest_api_operations_readiness_report(client: TestClient) -> None:
    """Test GET /api/v1/pre-deployment/operations-readiness/report endpoint."""
    response = client.get("/api/v1/pre-deployment/operations-readiness/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    content = response.text
    assert "# İzleme, Alarmlar, Olay Müdahale ve Rollback Dağıtım Öncesi Denetim Raporu" in content
    assert "GEÇTİ (PASS)" in content
    assert "OPS-MON-01" in content
