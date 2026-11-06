"""Comprehensive test suite for pre-deployment critical test verification and security gate."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.critical_verification_gatekeeper import (
    MANDATORY_CRITICAL_SUITES,
    CriticalSuiteCategory,
    CriticalSuiteDefinition,
    CriticalSuiteExecutionResult,
    CriticalTestCoordinator,
    CriticalVerificationGatekeeper,
    PreDeploymentSecurityCoordinator,
    SecurityScansVerificationResult,
    VerificationGateStatus,
)
from stock_analysis.security.legal_compliance import GateStatus, LegalComplianceGateReport
from stock_analysis.security.scanners import (
    CodeScanReport,
    ContainerScanReport,
    DependencyScanReport,
    SecuritySuiteReport,
)
from stock_analysis.security.secret_auditor import SecretAuditReport, SecretGateStatus


class TestCriticalTestCoordinator:
    """Tests for CriticalTestCoordinator mandatory suite tracking and evaluation."""

    def test_mandatory_suites_catalog(self) -> None:
        coordinator = CriticalTestCoordinator()
        assert len(coordinator.mandatory_suites) == len(MANDATORY_CRITICAL_SUITES)
        suite_ids = [s.suite_id for s in MANDATORY_CRITICAL_SUITES]
        assert "SUITE-UNIT-001" in suite_ids
        assert "SUITE-SEC-002" in suite_ids
        assert "SUITE-E2E-003" in suite_ids
        assert "SUITE-INT-004" in suite_ids
        assert "SUITE-SCHEMA-005" in suite_ids
        assert "SUITE-TIME-006" in suite_ids
        assert "SUITE-MODEL-007" in suite_ids

    def test_missing_mandatory_suite_fails_verification(self) -> None:
        coordinator = CriticalTestCoordinator()
        # Only record 1 suite
        coordinator.record_suite_result(
            CriticalSuiteExecutionResult(
                suite_id="SUITE-UNIT-001",
                suite_name="Unit Core Suite",
                category=CriticalSuiteCategory.UNIT_CORE,
                test_file="tests/test_unit_core_suite.py",
                tests_count=20,
                passed=20,
                failed=0,
            )
        )
        passed, results, issues = coordinator.verify_all_suites()
        assert passed is False
        assert len(results) == 1
        assert len(issues) >= 6  # The remaining suites are missing
        assert any("SUITE-SEC-002" in issue or "Security Controls" in issue for issue in issues)

    def test_failed_tests_in_suite_fails_verification(self) -> None:
        coordinator = CriticalTestCoordinator()
        coordinator.run_default_synthetic_manifest()
        # Inject failure into SUITE-SEC-002
        coordinator.record_suite_result(
            CriticalSuiteExecutionResult(
                suite_id="SUITE-SEC-002",
                suite_name="Security Controls",
                category=CriticalSuiteCategory.SECURITY,
                test_file="tests/test_security_suite.py",
                tests_count=20,
                passed=18,
                failed=2,
                status="FAIL",
                error_details=["AssertionError: Token signature invalid"],
            )
        )
        passed, results, issues = coordinator.verify_all_suites()
        assert passed is False
        assert any("Test paketi basarisiz: Security Controls" in issue for issue in issues)

    def test_insufficient_test_count_fails_verification(self) -> None:
        custom_suites = (
            CriticalSuiteDefinition(
                suite_id="SUITE-CUSTOM-001",
                name="Custom Suite",
                category=CriticalSuiteCategory.UNIT_CORE,
                test_file="tests/test_custom.py",
                min_test_count=15,
                description="Custom test suite",
            ),
        )
        coordinator = CriticalTestCoordinator(mandatory_suites=custom_suites)
        coordinator.record_suite_result(
            CriticalSuiteExecutionResult(
                suite_id="SUITE-CUSTOM-001",
                suite_name="Custom Suite",
                category=CriticalSuiteCategory.UNIT_CORE,
                test_file="tests/test_custom.py",
                tests_count=5,
                passed=5,
                failed=0,
            )
        )
        passed, results, issues = coordinator.verify_all_suites()
        assert passed is False
        assert any("Yetersiz test sayisi" in issue for issue in issues)

    def test_all_suites_passed_successfully(self) -> None:
        coordinator = CriticalTestCoordinator()
        manifest = coordinator.run_default_synthetic_manifest()
        assert len(manifest) == len(MANDATORY_CRITICAL_SUITES)
        passed, results, issues = coordinator.verify_all_suites()
        assert passed is True
        assert len(issues) == 0
        assert len(results) == len(MANDATORY_CRITICAL_SUITES)
        assert all(r.is_successful for r in results)


class TestPreDeploymentSecurityCoordinator:
    """Tests for PreDeploymentSecurityCoordinator multi-vector security scan aggregation."""

    def test_clean_security_scan_passes(self) -> None:
        # Mock scanner suite
        mock_scanner_suite = MagicMock()
        mock_scanner_suite.run_all.return_value = SecuritySuiteReport(
            dependency_report=DependencyScanReport(
                total_packages_checked=45, issues=[], sbom_generated=True
            ),
            container_report=ContainerScanReport(target="Dockerfile", rules_checked=14, issues=[]),
            code_report=CodeScanReport(files_scanned=50, lines_scanned=12000, issues=[]),
        )

        now = datetime.now(UTC)
        mock_secret_gatekeeper = MagicMock()
        mock_secret_gatekeeper.evaluate_gate.return_value = (
            SecretGateStatus.PASS,
            SecretAuditReport(
                started_at=now,
                completed_at=now,
                total_files_scanned=200,
                total_bytes_scanned=25000,
                findings=[],
            ),
        )

        mock_legal_gatekeeper = MagicMock()
        mock_legal_gatekeeper.evaluate_gate.return_value = LegalComplianceGateReport(
            overall_gate_status=GateStatus.PASS,
            total_documents=5,
            approved_documents=5,
            total_packages_audited=45,
            prohibited_licenses_count=0,
            spk_compliance_passed=True,
            kvkk_compliance_passed=True,
            disclaimer_coverage_passed=True,
            issues_found=[],
            audited_at=now.isoformat(),
        )

        coordinator = PreDeploymentSecurityCoordinator(
            scanner_suite=mock_scanner_suite,
            secret_gatekeeper=mock_secret_gatekeeper,
            legal_gatekeeper=mock_legal_gatekeeper,
        )

        result = coordinator.run_security_scans(repo_root=".")
        assert result.overall_security_passed is True
        assert result.sast_passed is True
        assert result.dependency_passed is True
        assert result.container_passed is True
        assert result.secrets_scan_passed is True
        assert result.legal_compliance_passed is True
        assert result.total_critical == 0
        assert result.total_high == 0
        assert len(result.issues) == 0

    def test_sast_critical_vulnerability_triggers_failure(self) -> None:
        mock_scanner_suite = MagicMock()
        mock_code_report = MagicMock()
        mock_code_report.passed = False
        mock_code_report.critical_count = 1
        mock_code_report.high_count = 0
        mock_code_report.files_scanned = 50

        mock_scanner_suite.run_all.return_value = SecuritySuiteReport(
            dependency_report=DependencyScanReport(
                total_packages_checked=45, issues=[], sbom_generated=True
            ),
            container_report=ContainerScanReport(target="Dockerfile", rules_checked=14, issues=[]),
            code_report=mock_code_report,
        )

        now = datetime.now(UTC)
        mock_secret_gatekeeper = MagicMock()
        mock_secret_gatekeeper.evaluate_gate.return_value = (
            SecretGateStatus.PASS,
            SecretAuditReport(
                started_at=now,
                completed_at=now,
                total_files_scanned=200,
                total_bytes_scanned=25000,
                findings=[],
            ),
        )

        mock_legal_gatekeeper = MagicMock()
        mock_legal_gatekeeper.evaluate_gate.return_value = LegalComplianceGateReport(
            overall_gate_status=GateStatus.PASS,
            total_documents=5,
            approved_documents=5,
            total_packages_audited=45,
            prohibited_licenses_count=0,
            spk_compliance_passed=True,
            kvkk_compliance_passed=True,
            disclaimer_coverage_passed=True,
            issues_found=[],
            audited_at=now.isoformat(),
        )

        coordinator = PreDeploymentSecurityCoordinator(
            scanner_suite=mock_scanner_suite,
            secret_gatekeeper=mock_secret_gatekeeper,
            legal_gatekeeper=mock_legal_gatekeeper,
        )

        result = coordinator.run_security_scans(repo_root=".")
        assert result.overall_security_passed is False
        assert result.sast_passed is False
        assert any("SAST" in issue for issue in result.issues)


class TestCriticalVerificationGatekeeper:
    """Tests for unified CriticalVerificationGatekeeper orchestration and reporting."""

    def test_evaluate_gate_full_pass(self) -> None:
        test_coord = CriticalTestCoordinator()
        test_coord.run_default_synthetic_manifest()

        mock_sec_coord = MagicMock()
        mock_sec_coord.run_security_scans.return_value = SecurityScansVerificationResult(
            sast_passed=True,
            sast_critical_count=0,
            sast_high_count=0,
            sast_files_scanned=60,
            dependency_passed=True,
            dependency_critical_count=0,
            dependency_high_count=0,
            sbom_generated=True,
            container_passed=True,
            container_rules_checked=14,
            secrets_scan_passed=True,
            secrets_detected_count=0,
            legal_compliance_passed=True,
            prohibited_licenses_count=0,
            spk_disclaimer_valid=True,
            overall_security_passed=True,
            issues=[],
        )

        gatekeeper = CriticalVerificationGatekeeper(
            test_coordinator=test_coord,
            security_coordinator=mock_sec_coord,
        )

        report = gatekeeper.evaluate_gate()
        assert report.gate_status == VerificationGateStatus.PASS
        assert report.sign_off_ready is True
        assert report.critical_tests_passed is True
        assert report.security_scans_passed is True
        assert report.total_tests_failed == 0
        assert report.security_critical_vulnerabilities == 0
        assert len(report.blocking_reasons) == 0

        # Verify markdown generation
        md_text = report.generate_markdown_audit_report()
        assert "# Dagitim Oncesi Kritik Test ve Guvenlik Dogrulama Raporu" in md_text
        assert "GECTI (PASS)" in md_text
        assert "SUITE-UNIT-001" in md_text
        assert "SUITE-SEC-002" in md_text

    def test_evaluate_gate_blocking_reasons_present(self) -> None:
        test_coord = CriticalTestCoordinator()
        # Leave test coordinator empty and disable auto-populate to test failure
        mock_sec_coord = MagicMock()
        mock_sec_coord.run_security_scans.return_value = SecurityScansVerificationResult(
            sast_passed=False,
            sast_critical_count=2,
            sast_high_count=1,
            sast_files_scanned=60,
            dependency_passed=True,
            dependency_critical_count=0,
            dependency_high_count=0,
            sbom_generated=True,
            container_passed=True,
            container_rules_checked=14,
            secrets_scan_passed=True,
            secrets_detected_count=0,
            legal_compliance_passed=True,
            prohibited_licenses_count=0,
            spk_disclaimer_valid=True,
            overall_security_passed=False,
            issues=["SAST kod taramasinda 2 CRITICAL, 1 HIGH acik tespit edildi."],
        )

        gatekeeper = CriticalVerificationGatekeeper(
            test_coordinator=test_coord,
            security_coordinator=mock_sec_coord,
        )

        report = gatekeeper.evaluate_gate(auto_populate_tests_if_empty=False)
        assert report.gate_status == VerificationGateStatus.FAIL
        assert report.sign_off_ready is False
        assert len(report.blocking_reasons) > 0

        md_text = report.generate_markdown_audit_report()
        assert "KALDI (FAIL)" in md_text
        assert "ENGEL:" in md_text


class TestPreDeploymentEndpoints:
    """Tests for FastAPI REST endpoints exposing verification gate and reports."""

    def test_get_pre_deployment_verify_endpoint(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/v1/pre-deployment/verify")
        assert response.status_code == 200
        data = response.json()
        assert "gate_status" in data
        assert data["gate_status"] in ("PASS", "FAIL")
        assert "timestamp" in data
        assert "critical_tests_passed" in data
        assert "security_scans_passed" in data
        assert "tests_summary" in data
        assert "test_suites" in data
        assert len(data["test_suites"]) >= len(MANDATORY_CRITICAL_SUITES)

    def test_get_pre_deployment_markdown_report_endpoint(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/v1/pre-deployment/report")
        assert response.status_code == 200
        assert "text/markdown" in response.headers.get("content-type", "")
        content = response.text
        assert "Dagitim Oncesi Kritik Test ve Guvenlik Dogrulama Raporu" in content
        assert "Kritik Test Paketleri Ozeti" in content
        assert "Cok Vektorlu Guvenlik Taramalari Ozeti" in content
