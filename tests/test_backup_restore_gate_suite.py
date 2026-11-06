"""Unit and integration test suite for Database Backup & Restoration pre-deployment gate (FAZ-16).

Tests:
1. Complete gate evaluation on operational multi-table database with 100% pass (8/8 checks).
2. Cryptographic encryption and PBKDF2-HMAC-SHA256 key derivation.
3. Tamper-evident envelope authentication and bit-flip rejection.
4. Unauthorized decryption key rejection without executing SQL.
5. Isolated sandbox reconstitution, table schema parity, and PRAGMA integrity_check.
6. RTO (Recovery Time Objective < 1 hour) and RPO compliance verification.
7. Markdown audit report generation for CI/CD and release sign-off.
8. Prometheus telemetry metrics tracking.
9. FastAPI REST endpoints (/pre-deployment/backup-restore/verify and /report).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from stock_analysis.api.app import create_app
from stock_analysis.security.backup_restore_gatekeeper import (
    BACKUP_RESTORE_GATE_STATUS,
    BACKUP_RESTORE_RTO_SECONDS,
    BackupRestoreCategory,
    BackupRestoreGatekeeper,
    BackupRestoreGateReport,
    BackupRestoreGateStatus,
)
from stock_analysis.security.database_backup import (
    DatabaseBackupEngine,
    DatabaseRestorationDrill,
    RestorationDrillResult,
    RestorationReport,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(create_app())


@pytest.fixture
def gatekeeper() -> BackupRestoreGatekeeper:
    """Create fresh isolated BackupRestoreGatekeeper instance."""
    engine = DatabaseBackupEngine(pbkdf2_iterations=1000)  # Fast for unit tests
    drill = DatabaseRestorationDrill(engine=engine)
    return BackupRestoreGatekeeper(engine=engine, drill=drill)


def test_backup_restore_default_gate_evaluation_success(
    gatekeeper: BackupRestoreGatekeeper,
) -> None:
    """Test that pre-deployment backup and restoration drill passes with 100% compliance."""
    report: BackupRestoreGateReport = gatekeeper.evaluate_gate(publish_report=True)

    assert report.gate_status == BackupRestoreGateStatus.PASS
    assert report.sign_off_ready is True
    assert report.total_checks == 8
    assert report.passed_checks == 8
    assert report.failed_checks == 0
    assert len(report.blocking_violations) == 0
    assert report.rto_compliant is True
    assert report.rto_seconds < 10.0  # Fast drill completed well under 1 hour
    assert report.rto_limit_seconds == 3600.0
    assert report.rpo_compliant is True
    assert report.tables_verified >= 5
    assert report.rows_verified >= 11
    assert report.published_report_path is not None
    assert Path(report.published_report_path).exists()


def test_backup_restore_categories_and_check_ids(
    gatekeeper: BackupRestoreGatekeeper,
) -> None:
    """Verify that all 8 expected check IDs and categories are present and passed."""
    report = gatekeeper.evaluate_gate(publish_report=False)
    check_ids = [c.check_id for c in report.checks]

    expected_ids = [
        "BKUP-ENC-01",
        "BKUP-MAC-02",
        "BKUP-ACC-03",
        "BKUP-SND-04",
        "BKUP-ROW-05",
        "BKUP-RTO-06",
        "BKUP-RPO-07",
        "BKUP-SPK-08",
    ]
    for exp_id in expected_ids:
        assert exp_id in check_ids

    # Verify category mappings
    categories = {c.category for c in report.checks}
    assert BackupRestoreCategory.ENCRYPTION_AND_KEY_DERIVATION in categories
    assert BackupRestoreCategory.TAMPER_DETECTION_AND_INTEGRITY in categories
    assert BackupRestoreCategory.ACCESS_CONTROL_AND_REJECTION in categories
    assert BackupRestoreCategory.SANDBOX_RECONSTITUTION in categories
    assert BackupRestoreCategory.RTO_RPO_RESILIENCE in categories
    assert BackupRestoreCategory.STATUTORY_COMPLIANCE in categories


def test_backup_restore_markdown_report_structure(
    gatekeeper: BackupRestoreGatekeeper,
) -> None:
    """Verify that the generated Markdown audit report satisfies compliance structure."""
    report = gatekeeper.evaluate_gate(publish_report=False)
    md_content = report.generate_markdown_audit_report()

    assert (
        "# Dağıtım Öncesi Yedekten Geri Yükleme ve Olağanüstü Durum Tatbikat Raporu" in md_content
    )
    assert "SPK Bilgi Sistemleri Tebliği VII-128.9 & KVKK m. 12" in md_content
    assert "🟢 **ONAYLANDI (PASS)**" in md_content
    assert "✅ Üretime Dağıtım İçin Uygundur" in md_content
    assert "## 1. Yönetici Özeti" in md_content
    assert "## 2. Denetim Metrikleri ve RTO/RPO Özeti" in md_content
    assert "## 3. Detaylı Kontrol Listesi" in md_content
    assert "## 4. Dağıtım Onayı" in md_content


def test_backup_restore_gate_fails_on_tamper_bypass() -> None:
    """Ensure gate fails if tamper detection cannot detect bit-flip corruptions."""
    mock_drill = MagicMock()
    mock_drill.run_drill.return_value = RestorationDrillResult(
        drill_id="mock-drill-01",
        timestamp=datetime.now(UTC),
        success=False,
        rto_seconds=0.25,
        rto_limit_seconds=3600.0,
        rto_compliant=True,
        rpo_compliant=True,
        tables_verified=5,
        rows_verified=15,
        tamper_detection_verified=False,  # Failed tamper test
        invalid_key_detection_verified=True,
        details="Tamper detection test failed",
        restoration_report=RestorationReport(
            manifest_id="mock-m",
            elapsed_seconds=0.1,
            integrity_status="PASSED",
            sqlite_pragma_check="ok",
            restored_tables={"users": 3},
            source_row_count=3,
            restored_row_count=3,
            tables_verified=1,
        ),
    )

    gk = BackupRestoreGatekeeper(drill=mock_drill)
    report = gk.evaluate_gate(publish_report=False)

    assert report.gate_status == BackupRestoreGateStatus.FAIL
    assert report.sign_off_ready is False
    assert any("BKUP-MAC-02" in violation for violation in report.blocking_violations)


def test_backup_restore_gate_fails_on_unauthorized_key_breach() -> None:
    """Ensure gate fails if wrong key is not rejected safely."""
    mock_drill = MagicMock()
    mock_drill.run_drill.return_value = RestorationDrillResult(
        drill_id="mock-drill-02",
        timestamp=datetime.now(UTC),
        success=False,
        rto_seconds=0.25,
        rto_limit_seconds=3600.0,
        rto_compliant=True,
        rpo_compliant=True,
        tables_verified=5,
        rows_verified=15,
        tamper_detection_verified=True,
        invalid_key_detection_verified=False,  # Failed key rejection
        details="Key rejection test failed",
        restoration_report=RestorationReport(
            manifest_id="mock-m",
            elapsed_seconds=0.1,
            integrity_status="PASSED",
            sqlite_pragma_check="ok",
            restored_tables={"users": 3},
            source_row_count=3,
            restored_row_count=3,
            tables_verified=1,
        ),
    )

    gk = BackupRestoreGatekeeper(drill=mock_drill)
    report = gk.evaluate_gate(publish_report=False)

    assert report.gate_status == BackupRestoreGateStatus.FAIL
    assert report.sign_off_ready is False
    assert any("BKUP-ACC-03" in violation for violation in report.blocking_violations)


def test_backup_restore_gate_fails_on_rto_limit_breach() -> None:
    """Ensure gate fails if restoration RTO exceeds statutory 1-hour SLA."""
    mock_drill = MagicMock()
    mock_drill.run_drill.return_value = RestorationDrillResult(
        drill_id="mock-drill-03",
        timestamp=datetime.now(UTC),
        success=False,
        rto_seconds=3650.0,  # Exceeds 3600s
        rto_limit_seconds=3600.0,
        rto_compliant=False,  # RTO breached
        rpo_compliant=True,
        tables_verified=5,
        rows_verified=15,
        tamper_detection_verified=True,
        invalid_key_detection_verified=True,
        details="RTO breached",
        restoration_report=RestorationReport(
            manifest_id="mock-m",
            elapsed_seconds=3650.0,
            integrity_status="PASSED",
            sqlite_pragma_check="ok",
            restored_tables={"users": 3},
            source_row_count=3,
            restored_row_count=3,
            tables_verified=1,
        ),
    )

    gk = BackupRestoreGatekeeper(drill=mock_drill)
    report = gk.evaluate_gate(publish_report=False)

    assert report.gate_status == BackupRestoreGateStatus.FAIL
    assert report.sign_off_ready is False
    assert any("BKUP-RTO-06" in violation for violation in report.blocking_violations)


def test_backup_restore_api_verify_endpoint(client: TestClient) -> None:
    """Test GET /pre-deployment/backup-restore/verify returns 200 with PASS report."""
    response = client.get("/api/v1/pre-deployment/backup-restore/verify")
    assert response.status_code == 200
    data = response.json()

    assert data["gate_status"] == "PASS"
    assert data["sign_off_ready"] is True
    assert data["total_checks"] == 8
    assert data["passed_checks"] == 8
    assert data["failed_checks"] == 0
    assert data["rto_compliant"] is True
    assert data["rpo_compliant"] is True
    assert data["tables_verified"] >= 5
    assert data["rows_verified"] >= 11
    assert len(data["checks"]) == 8


def test_backup_restore_api_report_markdown_endpoint(client: TestClient) -> None:
    """Test GET /api/v1/pre-deployment/backup-restore/report endpoint returns Markdown text."""
    response = client.get("/api/v1/pre-deployment/backup-restore/report")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert (
        "# Dağıtım Öncesi Yedekten Geri Yükleme ve Olağanüstü Durum Tatbikat Raporu"
        in response.text
    )
    assert "SPK Bilgi Sistemleri Tebliği" in response.text


def test_backup_restore_prometheus_telemetry(gatekeeper: BackupRestoreGatekeeper) -> None:
    """Verify that Prometheus metrics are updated upon gate evaluation."""
    report = gatekeeper.evaluate_gate(publish_report=False)

    assert report.gate_status == BackupRestoreGateStatus.PASS
    assert BACKUP_RESTORE_GATE_STATUS.get() == 1.0
    assert BACKUP_RESTORE_RTO_SECONDS.get() > 0.0
