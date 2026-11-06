"""Sürüm 5 (Beta ve Üretim) - SLO ve Destek Süreci Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 5 — Beta ve üretim:
  - SLO ve destek süreci aktif.
"""

from __future__ import annotations

from pathlib import Path

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.release.release_five_slo_support import (
    DEFAULT_SLO_SUPPORT_DISCLAIMER,
    RELEASE_FIVE_SLO_SUPPORT_STATUS,
    ReleaseFiveSloSupportGatekeeper,
    ReleaseFiveSloSupportGateStatus,
    SloSupportCheckItem,
    SloSupportVerificationResult,
    mask_sensitive_pii,
)


class TestReleaseFiveSloSupportSuite:
    """Sürüm 5 SLO ve destek süreci kabul testleri."""

    def test_check_availability_and_error_budget(self) -> None:
        """1. Sistem kullanılabilirliği SLO (>= %99.5) ve hata bütçesi kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_availability_and_error_budget()

        assert item.check_id == "REL5-SLO-01-AVAILABILITY-AND-ERROR-BUDGET"
        assert item.passed is True
        assert "Kullanılabilirlik" in item.details
        assert "Hata Bütçesi" in item.details
        assert item.metadata.get("is_availability_met") is True
        assert item.metadata.get("measured_availability_pct", 0.0) >= 99.5
        assert item.metadata.get("error_rate_pct", 1.0) <= 0.10
        assert item.metadata.get("error_budget_remaining_pct", 0.0) >= 50.0

    def test_check_api_latency_and_freshness(self) -> None:
        """2. API yanıt süresi ve veri güncelliği SLO kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_api_latency_and_freshness()

        assert item.check_id == "REL5-SLO-02-API-LATENCY-AND-DATA-FRESHNESS"
        assert item.passed is True
        assert "Önbellekli API p95" in item.details
        assert "Önbelleksiz API p95" in item.details
        assert item.metadata.get("is_cached_sla_met") is True
        assert item.metadata.get("is_uncached_sla_met") is True
        assert item.metadata.get("p95_cached_ms", 999.0) < 300.0
        assert item.metadata.get("p95_uncached_ms", 999.0) < 800.0
        assert item.metadata.get("freshness_status") in ("fresh", "FRESH")

    def test_check_support_channels_and_sla(self) -> None:
        """3. Destek kanalları, triage ve SLA yönlendirmesi kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_support_channels_and_sla()

        assert item.check_id == "REL5-SLO-03-SUPPORT-CHANNELS-AND-SLA-ROUTING"
        assert item.passed is True
        assert "destek@bora-analiz.local" in item.details
        assert "guvenlik@bora-analiz.local" in item.details
        assert item.metadata.get("urgent_sla_hours") == 4
        assert item.metadata.get("standard_sla_hours") == 24
        assert item.metadata.get("readiness_status") == "PASS"

    def test_check_spk_investment_advice_shield(self) -> None:
        """4. Destek iletişiminde SPK yatırım tavsiyesi kalkanı kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_spk_investment_advice_shield()

        assert item.check_id == "REL5-SLO-04-SPK-INVESTMENT-ADVICE-SHIELD"
        assert item.passed is True
        assert "TETİKLENDİ" in item.details
        assert item.metadata.get("disclaimer_triggered") is True
        assert item.metadata.get("has_spk_warning_note") is True
        assert item.metadata.get("has_global_disclaimer") is True

    def test_check_support_pii_privacy_shield(self) -> None:
        """5. Destek veri gizliliği ve PII maskeleme kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_support_pii_privacy_shield()

        assert item.check_id == "REL5-SLO-05-SUPPORT-PII-PRIVACY-SHIELD"
        assert item.passed is True
        assert item.metadata.get("mask_count", 0) >= 4
        assert item.metadata.get("is_tckn_masked") is True
        assert item.metadata.get("is_cc_masked") is True
        assert item.metadata.get("is_iban_masked") is True
        assert item.metadata.get("is_pw_masked") is True

    def test_check_incident_escalation_integration(self) -> None:
        """6. Destek taleplerinin olay yönetimine eskalasyonu kontrolü testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper()
        item: SloSupportCheckItem = gatekeeper._check_incident_escalation_integration()

        assert item.check_id == "REL5-SLO-06-INCIDENT-ESCALATION-INTEGRATION"
        assert item.passed is True
        assert item.metadata.get("incident_id", "").startswith("INC-")
        assert item.metadata.get("severity") in ("SEV_1", "SEV_1_CRITICAL")
        assert item.metadata.get("is_ticket_linked") is True

    def test_verify_all_full_pass(self, tmp_path: Path) -> None:
        """Tüm 6 SLO ve destek süreci kontrolünün PASS vermesi ve rapor üretimi testi."""
        gatekeeper = ReleaseFiveSloSupportGatekeeper(workspace_root=tmp_path)
        result: SloSupportVerificationResult = gatekeeper.verify_all()

        assert result.status == ReleaseFiveSloSupportGateStatus.PASS
        assert result.passed_count == 6
        assert result.total_count == 6
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert result.metadata.get("all_passed") is True
        assert RELEASE_FIVE_SLO_SUPPORT_STATUS.get() == 1.0

        # Rapor dosyasının varlığı ve içeriği
        report_file = tmp_path / "docs" / "audit" / "RELEASE_5_SLO_SUPPORT_REPORT.md"
        assert report_file.exists()
        content = report_file.read_text(encoding="utf-8")
        assert "Sürüm 5 — SLO ve Destek Süreci Kabul Denetim Raporu" in content
        assert "REL5-SLO-01-AVAILABILITY-AND-ERROR-BUDGET" in content
        assert "REL5-SLO-06-INCIDENT-ESCALATION-INTEGRATION" in content
        assert "SPK Mevzuati Uyarinca" in content

    def test_pii_masking_unit(self) -> None:
        """mask_sensitive_pii fonksiyonunun birim testi."""
        sample_text = (
            "Müşteri: 98765432109 nolu vatandaş, kart: 5526 1234 5678 9999, "
            "TR330006100511123456789012 ve parola: GizliSifre456!"
        )
        masked, count = mask_sensitive_pii(sample_text)

        assert count == 4
        assert "98765432109" not in masked
        assert "5526 1234 5678 9999" not in masked
        assert "TR330006100511123456789012" not in masked
        assert "GizliSifre456!" not in masked
        assert "***TCKN_MASKED***" in masked
        assert "***CREDIT_CARD_MASKED***" in masked
        assert "***IBAN_MASKED***" in masked
        assert "***PASSWORD_MASKED***" in masked

    def test_disclaimer_compliance(self) -> None:
        """DEFAULT_SLO_SUPPORT_DISCLAIMER yasal uyum metninin doğrulanması."""
        # validate_compliant_text kural dışı kelimelerde ValueError fırlatır
        validate_compliant_text(DEFAULT_SLO_SUPPORT_DISCLAIMER)
