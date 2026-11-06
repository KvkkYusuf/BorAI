"""Unit and integration test suite for Acceptance Criteria Gatekeeper (FAZ-16).

Verifies:
1. docs/ACCEPTANCE_CRITERIA.md document integrity and 13 mandatory criteria rows.
2. Failure handling when acceptance criteria file is missing or has missing required areas.
3. Itemized empirical evidence evaluation across all 13 criteria (13/13 PASS).
4. Gatekeeper evaluate_gate() overall evaluation (all_passed=True, gate_status="PASS").
5. Failure handling and blocking violations when a criterion fails.
6. Formal Markdown audit report generation and file persistence.
7. REST API endpoint /api/v1/system/pre-deployment/acceptance-criteria/verify.
8. REST API endpoint /api/v1/system/pre-deployment/acceptance-criteria/report.
9. Prometheus metrics instrumentation (status gauge, counters).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.acceptance_criteria_gatekeeper import (
    ACCEPTANCE_CRITERIA_EVALUATIONS_TOTAL,
    ACCEPTANCE_CRITERIA_GATE_STATUS,
    ACCEPTANCE_CRITERIA_PASSED_TOTAL,
    DEFAULT_ACCEPTANCE_CRITERIA_FILE,
    AcceptanceCriteriaGatekeeper,
    CriterionEvidence,
    CriterionStatus,
)


@pytest.fixture
def client() -> TestClient:
    """Create a FastAPI test client."""
    return TestClient(create_app())


@pytest.fixture
def gatekeeper() -> AcceptanceCriteriaGatekeeper:
    """Provide the default acceptance criteria gatekeeper."""
    return AcceptanceCriteriaGatekeeper()


def test_default_acceptance_criteria_document_exists_and_valid(
    gatekeeper: AcceptanceCriteriaGatekeeper,
) -> None:
    """Verify that docs/ACCEPTANCE_CRITERIA.md exists and contains all required areas."""
    assert DEFAULT_ACCEPTANCE_CRITERIA_FILE.exists()
    is_valid, checksum, violations = gatekeeper.verify_document_integrity()
    assert is_valid is True
    assert len(checksum) == 64
    assert len(violations) == 0


def test_missing_document_handling() -> None:
    """Verify failure behavior when acceptance criteria document does not exist."""
    missing_gatekeeper = AcceptanceCriteriaGatekeeper(
        criteria_path=Path("non_existent_criteria.md")
    )
    is_valid, checksum, violations = missing_gatekeeper.verify_document_integrity()
    assert is_valid is False
    assert checksum == ""
    assert any("bulunamadı" in v for v in violations)

    report = missing_gatekeeper.evaluate_gate(publish_report=False)
    assert report.all_passed is False
    assert report.gate_status == "FAIL"
    assert len(report.blocking_violations) > 0


def test_missing_required_area_handling() -> None:
    """Verify failure behavior when a mandatory acceptance area is missing from file."""
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(
            "# Incomplete Acceptance Criteria\n\n| Alan | Hedef |\n| Veri kalitesi | %0.5 |\n"
        )
        tmp_path = Path(tmp.name)

    try:
        custom_gatekeeper = AcceptanceCriteriaGatekeeper(criteria_path=tmp_path)
        is_valid, _, violations = custom_gatekeeper.verify_document_integrity()
        assert is_valid is False
        assert any("Zorunlu kabul ölçütü alanı eksik" in v for v in violations)

        report = custom_gatekeeper.evaluate_gate(publish_report=False)
        assert report.all_passed is False
        assert report.gate_status == "FAIL"
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def test_all_13_acceptance_criteria_pass(gatekeeper: AcceptanceCriteriaGatekeeper) -> None:
    """Verify that all 13 criteria have solid empirical evidence and PASS status."""
    evidences = gatekeeper.get_criterion_evidences()
    assert len(evidences) == 13

    for item in evidences:
        assert (
            item.status == CriterionStatus.PASS
        ), f"Criterion {item.criterion_id} failed: {item.details}"
        assert item.target_threshold != ""
        assert item.actual_value != ""
        assert item.phase_reference.startswith("FAZ-")
        assert item.evidence_source != ""
        assert item.details != ""


def test_evaluate_gate_full_success(gatekeeper: AcceptanceCriteriaGatekeeper) -> None:
    """Verify full evaluation of acceptance criteria pre-deployment gate."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_report = Path(tmp_dir) / "test_report.md"
        report = gatekeeper.evaluate_gate(publish_report=True, output_path=out_report)

        assert report.all_passed is True
        assert report.gate_status == "PASS"
        assert report.total_criteria == 13
        assert report.passed_criteria == 13
        assert report.failed_criteria == 0
        assert len(report.blocking_violations) == 0
        assert out_report.exists()

        content = out_report.read_text(encoding="utf-8")
        assert "# Kabul Ölçütleri Dağıtım Öncesi Denetim ve Kanıt Raporu" in content
        assert "CRIT-01-DATA-FRESHNESS" in content
        assert "CRIT-11-LLM-ZERO-COST" in content
        assert "0 TL" in content
        assert "🟢 GEÇTİ (PASS)" in content


def test_gatekeeper_failure_handling_on_failed_criterion(
    gatekeeper: AcceptanceCriteriaGatekeeper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that any single failing criterion trips the gate to FAIL."""
    original_evidences = gatekeeper.get_criterion_evidences()

    # Modify one criterion to FAIL
    failing_evidences = list(original_evidences)
    failing_evidences[2] = CriterionEvidence(
        criterion_id="CRIT-03-API-CACHED-LATENCY",
        area="API (önbellekli okuma)",
        criterion_name="p95 yanıt",
        target_threshold="< 300 ms",
        actual_value="450 ms",
        status=CriterionStatus.FAIL,
        evidence_source="simulated_latency_spike",
        phase_reference="FAZ-06",
        details="Latency exceeded acceptable target threshold.",
    )

    monkeypatch.setattr(gatekeeper, "get_criterion_evidences", lambda: failing_evidences)

    report = gatekeeper.evaluate_gate(publish_report=False)
    assert report.all_passed is False
    assert report.gate_status == "FAIL"
    assert report.failed_criteria == 1
    assert any("CRIT-03-API-CACHED-LATENCY" in v for v in report.blocking_violations)


def test_api_pre_deployment_acceptance_criteria_verify_endpoint(client: TestClient) -> None:
    """Verify REST API endpoint /api/v1/pre-deployment/acceptance-criteria/verify."""
    response = client.get("/api/v1/pre-deployment/acceptance-criteria/verify")
    assert response.status_code == 200

    data = response.json()
    assert data["gate_status"] == "PASS"
    assert data["all_passed"] is True
    assert data["total_criteria"] == 13
    assert data["passed_criteria"] == 13
    assert data["failed_criteria"] == 0
    assert len(data["criteria"]) == 13

    # Check specific critical items
    crit_ids = [c["criterion_id"] for c in data["criteria"]]
    assert "CRIT-01-DATA-FRESHNESS" in crit_ids
    assert "CRIT-08-SECURITY-VULNERABILITIES" in crit_ids
    assert "CRIT-10-MODEL-BASELINE-GUARD" in crit_ids
    assert "CRIT-11-LLM-ZERO-COST" in crit_ids


def test_api_pre_deployment_acceptance_criteria_report_endpoint(client: TestClient) -> None:
    """Verify REST API endpoint /api/v1/pre-deployment/acceptance-criteria/report."""
    response = client.get("/api/v1/pre-deployment/acceptance-criteria/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")

    text = response.text
    assert "# Kabul Ölçütleri Dağıtım Öncesi Denetim ve Kanıt Raporu" in text
    assert "Madde Madde Kabul Ölçütleri Kanıt Matrisi" in text
    assert "Yayın Onayı (Sign-Off)" in text


def test_acceptance_criteria_prometheus_metrics(gatekeeper: AcceptanceCriteriaGatekeeper) -> None:
    """Verify that Prometheus metrics update accurately upon evaluation."""
    initial_evals = ACCEPTANCE_CRITERIA_EVALUATIONS_TOTAL.get()
    initial_passed = ACCEPTANCE_CRITERIA_PASSED_TOTAL.get()

    report = gatekeeper.evaluate_gate(publish_report=False)
    assert report.all_passed is True

    assert ACCEPTANCE_CRITERIA_GATE_STATUS.get() == 1.0
    assert ACCEPTANCE_CRITERIA_EVALUATIONS_TOTAL.get() == initial_evals + 1
    assert ACCEPTANCE_CRITERIA_PASSED_TOTAL.get() == initial_passed + 13
