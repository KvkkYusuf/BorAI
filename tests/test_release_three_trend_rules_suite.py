"""Sürüm 3 Trend Kuralları Açıklanabilir ve Tekrar Üretilebilir Kabul Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 3 — Klasik analiz ve uyarı:
  - Trend kuralları açıklanabilir ve tekrar üretilebilir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.release.release_three_trend_rules import (
    RELEASE_THREE_TREND_RULES_STATUS,
    ReleaseThreeTrendGateStatus,
    ReleaseThreeTrendRulesGatekeeper,
    TrendRuleCheckItem,
    TrendRulesVerificationResult,
    generate_markdown_report,
)


class TestReleaseThreeTrendRulesSuite:
    """Sürüm 3 Trend Kuralları Doğrulama Kabul Kapısı test paketi."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        now = datetime.now(UTC)

        result: TrendRulesVerificationResult = gatekeeper.verify_release_three_trend_rules(now=now)

        assert result.status == ReleaseThreeTrendGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_THREE_TREND_RULES_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary
        assert result.evaluated_at == now

    def test_multi_indicator_defense_check(self) -> None:
        """Tek gösterge savunması ve bağımsız kategori gereksinimi kontrolü."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_multi_indicator_defense()

        assert item.check_id == "REL3-TREND-01-MULTI-IND-DEFENSE"
        assert item.passed is True
        assert "min_rules < 2 ValueError koruması: True" in item.details
        assert "min_cat < 2 ValueError koruması: True" in item.details

    def test_explainability_and_invalidation_check(self) -> None:
        """Kural açıklamaları, kategoriler ve invalidation kriterleri şeffaflığı kontrolü."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_explainability_and_invalidation()

        assert item.check_id == "REL3-TREND-02-EXPLAINABILITY-INVALIDATION"
        assert item.passed is True
        assert "Eksiksiz açıklandı" in item.details

    def test_legal_compliance_guardrails_check(self) -> None:
        """SPK kırmızı çizgi ve yasaklı terim yakalama kontrolleri."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_legal_compliance_guardrails()

        assert item.check_id == "REL3-TREND-03-LEGAL-GUARDRAILS"
        assert item.passed is True
        assert "'al' tuzağı yakalandı: True" in item.details
        assert "'hedef fiyat' tuzağı yakalandı: True" in item.details
        assert "'risksiz' tuzağı yakalandı: True" in item.details

    def test_cryptographic_reproducibility_check(self) -> None:
        """SHA-256 tekrarlanabilirlik hash'i ve determinizm kontrolü."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_cryptographic_reproducibility()

        assert item.check_id == "REL3-TREND-04-CRYPTOGRAPHIC-REPRODUCIBILITY"
        assert item.passed is True
        assert "10 bağımsız koşuda hash tekilliği: True" in item.details
        assert "Yön determinizmi: True" in item.details

    def test_cooldown_and_deduplication_check(self) -> None:
        """Sinyal soğuma (cooldown) ve tekilleştirme mekanizması kontrolü."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_cooldown_and_deduplication()

        assert item.check_id == "REL3-TREND-05-COOLDOWN-DEDUPLICATION"
        assert item.passed is True
        assert "cooldown_active=True" in item.details
        assert "cooldown_active=False" in item.details

    def test_liquidity_quality_filter_transparency_check(self) -> None:
        """Likidite ve veri kalitesi filtreleri red şeffaflığı kontrolü."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        item: TrendRuleCheckItem = gatekeeper._check_liquidity_quality_filter_transparency()

        assert item.check_id == "REL3-TREND-06-LIQUIDITY-QUALITY-FILTERS"
        assert item.passed is True
        assert "Kısa seri filtresi: True" in item.details
        assert "Düşük hacim filtresi: True" in item.details
        assert "Bozuk OHLC filtresi: True" in item.details

    def test_markdown_report_generation(self, tmp_path: Path) -> None:
        """Rapor oluşturma ve dosya yazma doğrulaması."""
        gatekeeper = ReleaseThreeTrendRulesGatekeeper()
        result = gatekeeper.verify_release_three_trend_rules()
        report_file = tmp_path / "RELEASE_3_TREND_RULES_REPORT.md"

        content = generate_markdown_report(result, report_path=report_file)

        assert report_file.exists()
        assert "# Sürüm 3 (Klasik Analiz ve Uyarı) — Trend Kuralları" in content
        assert "REL3-TREND-01-MULTI-IND-DEFENSE" in content
        assert "REL3-TREND-04-CRYPTOGRAPHIC-REPRODUCIBILITY" in content
        assert "✅ PASS" in content
        assert "- [x] Trend kuralları açıklanabilir ve tekrar üretilebilir." in content

    def test_prohibited_terms_trap_coverage(self) -> None:
        """Tüm yasaklı finansal yönlendirme terimlerinin yakalandığının doğrudan testi."""
        violations = [
            "Bu kağıdı hemen al",
            "Portföyü sat tavsiyesi",
            "hisse için hedef fiyat 150 TL",
            "kesinlikle kazanacaksınız",
            "garanti getiri",
            "bu işlem risksizdir",
        ]
        for v in violations:
            with pytest.raises(ValueError, match="Yasaklı finansal tavsiye/kesinlik"):
                validate_compliant_text(v)
