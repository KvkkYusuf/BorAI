"""Sürüm 5 (Beta ve Üretim) - Canlı Model Gölge Modu (Shadow Mode) Test Süiti.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 5 — Beta ve üretim:
  - Canlı model önce gölge modda izlendi.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from stock_analysis.models.shadow_mode import (
    DEFAULT_SHADOW_MODE_DISCLAIMER,
)
from stock_analysis.release.release_five_shadow_mode import (
    RELEASE_FIVE_SHADOW_MODE_STATUS,
    ReleaseFiveShadowGateStatus,
    ReleaseFiveShadowModeGatekeeper,
    ShadowModeCheckItem,
    ShadowModeVerificationResult,
    generate_markdown_report,
)


class TestReleaseFiveShadowModeSuite:
    """Sürüm 5 canlı model gölge modu kabul testleri."""

    def test_check_shadow_tracking_active(self) -> None:
        """1. Gölge modu kullanıcı trafiği izolasyonu ve aktif takip testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_shadow_tracking_active()

        assert item.check_id == "REL5-SHD-01-SHADOW-TRACKING-ACTIVE"
        assert item.passed is True
        assert "bist_lstm_v2_candidate" in item.details
        assert "sıfır sızıntı" in item.details
        assert item.metadata.get("observations_recorded") == 12
        assert item.metadata.get("shadow_isolation_active") is True

    def test_check_baseline_comparison(self) -> None:
        """2. Naif baseline karşılaştırması ve yön doğruluğu testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_baseline_comparison()

        assert item.check_id == "REL5-SHD-02-BASELINE-BENCHMARK-BEATEN"
        assert item.passed is True
        assert "Model MAE" in item.details
        assert "naif baseline MAE" in item.details
        assert item.metadata.get("baseline_beaten") is True
        assert item.metadata.get("model_mae") is not None
        assert item.metadata.get("baseline_mae") is not None
        assert item.metadata["model_mae"] < item.metadata["baseline_mae"]
        assert item.metadata.get("model_directional_accuracy", 0.0) >= 0.50

    def test_check_latency_sla_compliance(self) -> None:
        """3. Çıkarım gecikmesi p95/p99 ve hata oranı SLA testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_latency_sla_compliance()

        assert item.check_id == "REL5-SHD-03-LATENCY-SLA-COMPLIANCE"
        assert item.passed is True
        assert "p95" in item.details
        assert "SLA tavanı" in item.details
        assert item.metadata.get("sla_compliant") is True
        assert item.metadata.get("latency_p95_ms", 999.0) <= 200.0
        assert item.metadata.get("latency_p99_ms", 999.0) <= 500.0
        assert item.metadata.get("error_rate", 1.0) <= 0.01

    def test_check_confidence_interval_coverage(self) -> None:
        """4. Belirsizlik aralığı gerçekleşme kapsaması (%90 CI) testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_confidence_interval_coverage()

        assert item.check_id == "REL5-SHD-04-UNCERTAINTY-INTERVAL-COVERAGE"
        assert item.passed is True
        assert "kapsaması" in item.details
        assert "Epistemic uncertainty" in item.details
        assert item.metadata.get("interval_coverage", 0.0) >= 0.65
        assert item.metadata.get("uncertainty_telemetry_complete") is True

    def test_check_promotion_gate_enforcement(self) -> None:
        """5. Terfi kapısı kuralları ve durum geçişleri (EXTEND/REJECT/PROCEED) testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_promotion_gate_enforcement()

        assert item.check_id == "REL5-SHD-05-PROMOTION-GATE-ENFORCEMENT"
        assert item.passed is True
        assert "EXTEND_SHADOW" in item.details
        assert "REJECT" in item.details
        assert "PROCEED_TO_LIVE" in item.details
        assert item.metadata.get("pending_handled") is True
        assert item.metadata.get("reject_handled") is True
        assert item.metadata.get("promotion_handled") is True

    def test_check_compliance_and_audit_report(self) -> None:
        """6. SPK uyumlu yasal uyarı ve denetim raporu formatı testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        item: ShadowModeCheckItem = gatekeeper._check_compliance_and_audit_report()

        assert item.check_id == "REL5-SHD-06-COMPLIANCE-TELEMETRY-AUDIT"
        assert item.passed is True
        assert "SPK" in item.details
        assert item.metadata.get("spk_disclaimer_verified") is True
        assert item.metadata.get("report_template_verified") is True

    def test_verify_all_gatekeeper_pass(self) -> None:
        """Tüm 6 kontrolün eksiksiz PASS olması ve telemetri doğrulaması."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()
        result: ShadowModeVerificationResult = gatekeeper.verify_all()

        assert result.status == ReleaseFiveShadowGateStatus.PASS
        assert result.passed_count == 6
        assert result.total_count == 6
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert result.metadata.get("all_passed") is True
        assert "# Sürüm 5 — Canlı Model Gölge Modu" in result.report_markdown
        assert DEFAULT_SHADOW_MODE_DISCLAIMER in result.report_markdown
        assert RELEASE_FIVE_SHADOW_MODE_STATUS.get() == 1.0

    def test_verify_all_gatekeeper_failure_handling(self) -> None:
        """Kontrollerden biri başarısız olduğunda FAIL durumuna düşme testi."""
        gatekeeper = ReleaseFiveShadowModeGatekeeper()

        with patch.object(
            gatekeeper,
            "_check_latency_sla_compliance",
            return_value=ShadowModeCheckItem(
                check_id="REL5-SHD-03-LATENCY-SLA-COMPLIANCE",
                name="Çıkarım Gecikmesi ve SLA Uyumu",
                passed=False,
                details="Mock SLA gecikme aşımı",
            ),
        ):
            result: ShadowModeVerificationResult = gatekeeper.verify_all()

            assert result.status == ReleaseFiveShadowGateStatus.FAIL
            assert result.passed_count == 5
            assert result.total_count == 6
            assert result.metadata.get("all_passed") is False
            assert "❌ FAIL" in result.report_markdown

    def test_generate_markdown_report_structure(self) -> None:
        """Rapor Markdown formatının bölümleri ve tablo yapısı testi."""
        checks = [
            ShadowModeCheckItem(
                check_id="TEST-01",
                name="Test Kontrol",
                passed=True,
                details="Test detay",
            )
        ]
        report = generate_markdown_report(
            status=ReleaseFiveShadowGateStatus.PASS,
            checks=checks,
            passed_count=1,
            total_count=1,
            workspace_root=Path.cwd(),
        )

        assert "# Sürüm 5 — Canlı Model Gölge Modu (Shadow Mode) Denetim Raporu" in report
        assert "## 1. Yönetici Özeti" in report
        assert "## 2. Kabul Kontrolleri Matrisi" in report
        assert "## 3. Gölge Modu Performans ve Güvenilirlik Ölçütleri" in report
        assert "## 4. Yasal SPK Uyarısı" in report
        assert DEFAULT_SHADOW_MODE_DISCLAIMER in report
