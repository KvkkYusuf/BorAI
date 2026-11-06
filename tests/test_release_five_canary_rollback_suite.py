"""Sürüm 5 (Beta ve Üretim) - Kademeli Yayın ve Rollback Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 5 — Beta ve üretim:
  - Kademeli yayın ve rollback test edildi.
"""

from __future__ import annotations

from pathlib import Path

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.release.release_five_canary_rollback import (
    DEFAULT_CANARY_ROLLBACK_DISCLAIMER,
    RELEASE_FIVE_CANARY_ROLLBACK_STATUS,
    CanaryRollbackCheckItem,
    CanaryRollbackVerificationResult,
    ReleaseFiveCanaryGateStatus,
    ReleaseFiveCanaryRollbackGatekeeper,
    compute_deterministic_user_bucket,
    generate_markdown_report,
)


class TestReleaseFiveCanaryRollbackSuite:
    """Sürüm 5 kademeli yayın ve rollback kabul testleri."""

    def test_check_phased_canary_ladder(self) -> None:
        """1. Kademeli canary basamakları (%5 -> %25 -> %50 -> GA %100 -> STABLE) testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_phased_canary_ladder()

        assert item.check_id == "REL5-CR-01-PHASED-CANARY-LADDER"
        assert item.passed is True
        assert "Kademeli basamaklar" in item.details
        assert item.metadata.get("stages_verified") == [5.0, 25.0, 50.0, 100.0]
        assert item.metadata.get("final_status") == "stable"
        assert item.metadata.get("is_stable") is True

    def test_check_deterministic_cohort_isolation(self) -> None:
        """2. Deterministik tuzlu SHA-256 kohort izolasyonu ve süreklilik (sticky) testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_deterministic_cohort_isolation()

        assert item.check_id == "REL5-CR-02-DETERMINISTIC-COHORT-ISOLATION"
        assert item.passed is True
        assert item.metadata.get("is_deterministic") is True
        assert item.metadata.get("is_subset_5_in_25") is True
        assert item.metadata.get("is_subset_25_in_50") is True
        assert item.metadata.get("cohort_5_size", 0) <= item.metadata.get("cohort_25_size", 0)
        assert item.metadata.get("cohort_25_size", 0) <= item.metadata.get("cohort_50_size", 0)

    def test_check_automated_sla_rollback_triggers(self) -> None:
        """3. Otomatik metrik ve SLA hata eşiği tetikleyicileri testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_automated_sla_rollback_triggers()

        assert item.check_id == "REL5-CR-03-AUTOMATED-SLA-ROLLBACK-TRIGGERS"
        assert item.passed is True
        assert item.metadata.get("err_triggered") is True
        assert item.metadata.get("lat_triggered") is True
        assert item.metadata.get("is_rolled_back") is True
        assert item.metadata.get("rollback_reason") == "error_rate_breach"

    def test_check_emergency_operator_killswitch(self) -> None:
        """4. Acil durum operatör devre kesici ve anlık manuel rollback testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_emergency_operator_killswitch()

        assert item.check_id == "REL5-CR-04-EMERGENCY-OPERATOR-KILLSWITCH"
        assert item.passed is True
        assert item.metadata.get("is_instant_kill") is True
        assert item.metadata.get("canary_percentage_after") == 0.0
        assert item.metadata.get("operator") == "sre_lead_operator"
        assert item.metadata.get("reason") == "manual_override"

    def test_check_post_rollback_state_integrity(self) -> None:
        """5. Rollback sonrası RTO süresi, önbellek tahliyesi ve kararlı fallback testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_post_rollback_state_integrity()

        assert item.check_id == "REL5-CR-05-POST-ROLLBACK-STATE-INTEGRITY"
        assert item.passed is True
        assert item.metadata.get("rto_met") is True
        assert item.metadata.get("elapsed_ms", 9999.0) < 5000.0
        assert item.metadata.get("fallback_version") == "v4.2.0-stable"
        assert item.metadata.get("cache_purged") is True

    def test_check_regulatory_compliance_and_audit(self) -> None:
        """6. SPK mevzuat uyumu ve yasal çekince kontrolü testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper()
        item: CanaryRollbackCheckItem = gatekeeper._check_regulatory_compliance_and_audit()

        assert item.check_id == "REL5-CR-06-REGULATORY-COMPLIANCE-AUDIT-TRAIL"
        assert item.passed is True
        assert item.metadata.get("has_spk_notice") is True
        assert item.metadata.get("has_no_advice") is True
        validate_compliant_text(DEFAULT_CANARY_ROLLBACK_DISCLAIMER)

    def test_full_gate_evaluation_pass(self, tmp_path: Path) -> None:
        """7. Kabul kapısının 6/6 PASS ile sonuçlanması ve resmi rapor üretimi testi."""
        gatekeeper = ReleaseFiveCanaryRollbackGatekeeper(workspace_root=tmp_path)
        result: CanaryRollbackVerificationResult = gatekeeper.evaluate_gate()

        assert result.status == ReleaseFiveCanaryGateStatus.PASS
        assert result.total_checks == 6
        assert result.passed_checks == 6
        assert result.failed_checks == 0
        assert len(result.checks) == 6

        report_file = Path(result.audit_report_path)
        assert report_file.exists()
        content = report_file.read_text(encoding="utf-8")
        assert "# Sürüm 5 — Kademeli Yayın ve Rollback Kabul Denetim Raporu" in content
        assert "REL5-CR-01-PHASED-CANARY-LADDER" in content
        assert "REL5-CR-06-REGULATORY-COMPLIANCE-AUDIT-TRAIL" in content
        assert "SPK" in content

        assert RELEASE_FIVE_CANARY_ROLLBACK_STATUS.get() == 1.0

    def test_compute_deterministic_user_bucket_properties(self) -> None:
        """8. Deterministik kullanıcı kova eşleme fonksiyonunun sınır ve tutarlılık testi."""
        user = "investor_42@example.com"
        bucket_1 = compute_deterministic_user_bucket(user)
        bucket_2 = compute_deterministic_user_bucket(user)

        assert bucket_1 == bucket_2
        assert 0.0 <= bucket_1 < 100.0

        diff_bucket = compute_deterministic_user_bucket("another_investor_99@example.com")
        assert 0.0 <= diff_bucket < 100.0

    def test_markdown_report_formatting(self, tmp_path: Path) -> None:
        """9. Denetim raporu formatlama ve içerik doğrulaması testi."""
        checks = [
            CanaryRollbackCheckItem(
                check_id="REL5-CR-TEST-01",
                name="Test Kontrolü",
                passed=True,
                details="Test detay",
            )
        ]
        report = generate_markdown_report(
            status=ReleaseFiveCanaryGateStatus.PASS,
            checks=checks,
            passed_count=1,
            total_count=1,
            workspace_root=tmp_path,
        )
        assert "Test Kontrolü" in report
        assert "✅ PASS" in report
        assert "Yasal SPK Uyarısı" in report
