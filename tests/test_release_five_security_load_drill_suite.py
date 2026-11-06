"""Sürüm 5 (Beta ve Üretim) - Güvenlik, Yük Testi, Yedek ve Olay Tatbikatı Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 5 — Beta ve üretim:
  - Güvenlik incelemesi, yük testi, yedek geri yükleme ve olay tatbikatı tamamlandı.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from stock_analysis.release.release_five_security_load_drill import (
    DEFAULT_SLD_DISCLAIMER,
    ReleaseFiveSecurityLoadDrillGatekeeper,
    ReleaseFiveSLDGateStatus,
    SecurityLoadDrillCheckItem,
    SecurityLoadDrillVerificationResult,
    generate_markdown_report,
)


class TestReleaseFiveSecurityLoadDrillSuite:
    """Sürüm 5 güvenlik, yük testi, yedek ve olay tatbikatı kabul testleri."""

    def test_check_security_review_audit(self) -> None:
        """1. Güvenlik incelemesi ve çok vektörlü tarama kontrolü testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_security_review_audit()

        assert item.check_id == "REL5-SLD-01-SECURITY-REVIEW"
        assert item.passed is True
        assert "SecretAuditor" in item.details
        assert "HSTS" in item.details
        assert "OWASP" in item.details
        assert item.metadata.get("secret_gate_status") == "PASS"
        assert item.metadata.get("frame_options") == "DENY"
        assert item.metadata.get("owasp_controls_count") == 10

    def test_check_load_test_sla(self) -> None:
        """2. Yük testi ve performans SLA kontrolü testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_load_test_sla()

        assert item.check_id == "REL5-SLD-02-LOAD-TEST-SLA"
        assert item.passed is True
        assert "Önbellekli p95" in item.details
        assert "Önbelleksiz p95" in item.details
        assert item.metadata.get("is_cached_sla_met") is True
        assert item.metadata.get("is_uncached_sla_met") is True
        assert item.metadata.get("is_error_rate_sla_met") is True
        assert item.metadata.get("p95_cached_ms", 999.0) < 300.0
        assert item.metadata.get("p95_uncached_ms", 999.0) < 800.0

    def test_check_database_backup_integrity(self) -> None:
        """3. Kriptografik yedekleme ve kurcalama tespiti testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_database_backup_integrity()

        assert item.check_id == "REL5-SLD-03-DATABASE-BACKUP-INTEGRITY"
        assert item.passed is True
        assert "ChaCha20-Poly1305" in item.details
        assert item.metadata.get("tamper_detection_verified") is True
        assert item.metadata.get("wrong_key_rejected") is True
        assert item.metadata.get("kdf_iterations") == 100000

    def test_check_restore_drill_sandbox(self) -> None:
        """4. İzole sandbox yedekten geri yükleme tatbikatı testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_restore_drill_sandbox()

        assert item.check_id == "REL5-SLD-04-RESTORE-DRILL-SANDBOX"
        assert item.passed is True
        assert "PRAGMA integrity_check" in item.details
        assert item.metadata.get("rto_compliant") is True
        assert item.metadata.get("rpo_compliant") is True
        assert item.metadata.get("tables_verified", 0) >= 3
        assert item.metadata.get("rows_verified", 0) > 0

    def test_check_incident_response_drill(self) -> None:
        """5. Güvenlik olay tatbikatı ve müdahale senaryosu testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_incident_response_drill()

        assert item.check_id == "REL5-SLD-05-INCIDENT-RESPONSE-DRILL"
        assert item.passed is True
        assert "NIST SP 800-61" in item.details
        assert item.metadata.get("final_stage") == "LESSONS_LEARNED"
        assert item.metadata.get("containment_success") is True
        assert "KVKK" in item.metadata.get("external_authorities", [])
        assert "SPK" in item.metadata.get("external_authorities", [])

    def test_check_compliance_and_audit_report(self) -> None:
        """6. SPK mevzuat uyarısı ve denetim raporu kontrolü testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        item: SecurityLoadDrillCheckItem = gatekeeper._check_compliance_and_audit_report()

        assert item.check_id == "REL5-SLD-06-COMPLIANCE-AUDIT-REPORT"
        assert item.passed is True
        assert item.metadata.get("disclaimer_valid") is True
        assert item.metadata.get("report_dir_exists") is True
        assert DEFAULT_SLD_DISCLAIMER in item.metadata.get("disclaimer_text", "")

    def test_verify_all_full_pass(self) -> None:
        """Tüm 6 kontrolün eksiksiz PASS ile sonuçlanması testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        result: SecurityLoadDrillVerificationResult = gatekeeper.verify_all()

        assert result.status == ReleaseFiveSLDGateStatus.PASS
        assert result.passed_count == 6
        assert result.total_count == 6
        assert len(result.checks) == 6
        assert result.metadata.get("all_passed") is True
        assert "Sürüm 5 — Güvenlik İncelemesi" in result.report_markdown

    def test_markdown_report_structure(self) -> None:
        """Oluşturulan Markdown denetim raporunun yapısı testi."""
        checks = [
            SecurityLoadDrillCheckItem(
                check_id="REL5-SLD-01-SECURITY-REVIEW",
                name="Güvenlik İncelemesi",
                passed=True,
                details="SecretAuditor temiz.",
            ),
            SecurityLoadDrillCheckItem(
                check_id="REL5-SLD-02-LOAD-TEST-SLA",
                name="Yük Testi SLA",
                passed=True,
                details="SLA sağlandı.",
            ),
        ]
        mock_workspace = MagicMock()
        report = generate_markdown_report(
            status=ReleaseFiveSLDGateStatus.PASS,
            checks=checks,
            passed_count=2,
            total_count=2,
            workspace_root=mock_workspace,
        )

        assert "# Sürüm 5 — Güvenlik İncelemesi" in report
        assert "REL5-SLD-01-SECURITY-REVIEW" in report
        assert "REL5-SLD-02-LOAD-TEST-SLA" in report
        assert "SPK Mevzuati Uyarinca" in report
        assert "✅ PASS" in report

    def test_security_review_failure_handling(self) -> None:
        """Hata durumunda güvenli FAIL dönüş testi."""
        gatekeeper = ReleaseFiveSecurityLoadDrillGatekeeper()
        with patch.object(gatekeeper, "_check_security_review_audit") as mock_chk:
            mock_chk.return_value = SecurityLoadDrillCheckItem(
                check_id="REL5-SLD-01-SECURITY-REVIEW",
                name="Güvenlik İncelemesi",
                passed=False,
                details="Kritik açık tespit edildi.",
            )
            result = gatekeeper.verify_all()
            assert result.status == ReleaseFiveSLDGateStatus.FAIL
            assert result.passed_count < result.total_count
