"""Sürüm 4 (PyTorch Araştırma Hattı) - Başarısız Model Karantina Kabul Kapısı Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Başarısız model kullanıcıya sunulmuyor.
"""

from __future__ import annotations

from unittest.mock import patch

from stock_analysis.release.release_four_failed_model import (
    FailedModelCheckItem,
    FailedModelVerificationResult,
    ReleaseFourFailedModelGatekeeper,
    ReleaseFourFailedModelGateStatus,
    generate_markdown_report,
)


class TestReleaseFourFailedModelSuite:
    """Sürüm 4 başarısız model karantina kabul kriterleri testleri."""

    def test_check_governance_lifecycle_containment(self) -> None:
        """Model yönetişim ve yaşam döngüsü aşamaları kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_governance_lifecycle_containment()

        assert item.check_id == "REL4-FAIL-01-GOVERNANCE-LIFECYCLE"
        assert item.passed is True
        assert "APPROVED" in item.details
        assert "ACTIVE" in item.details
        assert item.metadata.get("rejection_tracking_active") is True
        assert "CANDIDATE" in item.metadata.get("blocked_stages", [])
        assert "REJECTED" in item.metadata.get("blocked_stages", [])

    def test_check_baseline_rejection_containment(self) -> None:
        """Naif baseline ve maliyet kapısı ret güvencesi kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_baseline_rejection_containment()

        assert item.check_id == "REL4-FAIL-02-BASELINE-REJECTION-POLICY"
        assert item.passed is True
        assert "başarıyla reddedildi" in item.details
        assert item.metadata.get("is_approved") is False
        assert len(item.metadata.get("rejection_reasons", [])) > 0

    def test_check_silent_fallback_prohibition(self) -> None:
        """Sessiz fallback yasağı ve şeffaf kullanıcı bildirimi kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_silent_fallback_prohibition()

        assert item.check_id == "REL4-FAIL-03-SILENT-FALLBACK-PROHIBITION"
        assert item.passed is True
        assert "Sessizce eski/uydurma tahmin üretimi mutlak olarak engellendi" in item.details
        assert item.metadata.get("forecast_available") is False
        assert item.metadata.get("tampered_value_rejected") is True
        assert "RSI_14" in item.metadata.get("alternative_indicators", [])

    def test_check_circuit_breaker_and_quarantine(self) -> None:
        """Devre kesici ve anomali karantinası kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_circuit_breaker_and_quarantine()

        assert item.check_id == "REL4-FAIL-04-CIRCUIT-BREAKER-QUARANTINE"
        assert item.passed is True
        assert "OPEN" in item.details
        assert item.metadata.get("circuit_state") == "OPEN"
        assert item.metadata.get("failure_threshold") == 3
        assert item.metadata.get("circuit_breaker_triggered") is True
        assert item.metadata.get("drift_quarantine_active") is True

    def test_check_shadow_mode_isolation(self) -> None:
        """Gölge modu izolasyonu ve trafik sızıntı koruması kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_shadow_mode_isolation()

        assert item.check_id == "REL4-FAIL-05-SHADOW-MODE-ISOLATION"
        assert item.passed is True
        assert item.metadata.get("decision") == "REJECT"
        assert item.metadata.get("is_approved_for_live") is False
        assert item.metadata.get("total_observations") == 5

    def test_check_regulatory_compliance_and_disclaimer(self) -> None:
        """SPK yasal uyarı ve kırmızı çizgi koruması kontrolü."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        item: FailedModelCheckItem = gatekeeper._check_regulatory_compliance_and_disclaimer()

        assert item.check_id == "REL4-FAIL-06-REGULATORY-COMPLIANCE"
        assert item.passed is True
        assert item.metadata.get("disclaimer_verified") is True
        assert item.metadata.get("prohibited_filter_active") is True

    def test_verify_all_full_gate_passed(self) -> None:
        """Tüm 6 kontrolün eksiksiz geçip kapının PASSED durumuna ulaştığını doğrular."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()
        result: FailedModelVerificationResult = gatekeeper.verify_all()

        assert result.status == ReleaseFourFailedModelGateStatus.PASSED
        assert result.passed_count == 6
        assert result.total_count == 6
        assert result.is_passed() is True
        assert len(result.checks) == 6
        assert "spk mevzuati uyarinca" in result.disclaimer.lower()
        assert "# Sürüm 4" in result.report_markdown
        assert "REL4-FAIL-01-GOVERNANCE-LIFECYCLE" in result.report_markdown
        assert "REL4-FAIL-02-BASELINE-REJECTION-POLICY" in result.report_markdown
        assert "REL4-FAIL-03-SILENT-FALLBACK-PROHIBITION" in result.report_markdown
        assert "REL4-FAIL-04-CIRCUIT-BREAKER-QUARANTINE" in result.report_markdown
        assert "REL4-FAIL-05-SHADOW-MODE-ISOLATION" in result.report_markdown
        assert "REL4-FAIL-06-REGULATORY-COMPLIANCE" in result.report_markdown

    def test_gatekeeper_failure_scenario(self) -> None:
        """Herhangi bir kontrol başarısız olduğunda kapının FAILED durumuna geçtiğini test eder."""
        gatekeeper = ReleaseFourFailedModelGatekeeper()

        with patch.object(
            gatekeeper,
            "_check_silent_fallback_prohibition",
            return_value=FailedModelCheckItem(
                check_id="REL4-FAIL-03-SILENT-FALLBACK-PROHIBITION",
                name="Sessiz Fallback Yasağı",
                passed=False,
                details="Sessiz fallback hatası simüle edildi.",
            ),
        ):
            result = gatekeeper.verify_all()
            assert result.status == ReleaseFourFailedModelGateStatus.FAILED
            assert result.passed_count == 5
            assert result.total_count == 6
            assert result.is_passed() is False

    def test_generate_markdown_report_structure(self) -> None:
        """Rapor formatının gerekli başlık ve tabloları içerdiğini test eder."""
        checks = [
            FailedModelCheckItem(
                check_id="REL4-FAIL-TEST",
                name="Test Check",
                passed=True,
                details="Test detay",
            )
        ]
        report = generate_markdown_report(
            status=ReleaseFourFailedModelGateStatus.PASSED,
            passed_count=1,
            total_count=1,
            checks=checks,
            disclaimer="Yasal uyarı test metni",
        )
        assert "# Sürüm 4" in report
        assert "REL4-FAIL-TEST" in report
        assert "PASS ✅" in report
        assert "Yasal uyarı test metni" in report
