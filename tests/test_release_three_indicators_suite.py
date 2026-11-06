"""Sürüm 3 Göstergeler Bağımsız Referanslarla Doğrulama Kabul Kapısı Test Paketi.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 3 — Klasik analiz ve uyarı:
  - Göstergeler bağımsız referanslarla doğrulandı.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_analysis.release.release_three_indicators import (
    RELEASE_THREE_INDICATORS_STATUS,
    IndicatorsVerificationResult,
    IndicatorVerificationCheckItem,
    ReleaseThreeIndGateStatus,
    ReleaseThreeIndicatorsGatekeeper,
    generate_markdown_report,
)


class TestReleaseThreeIndicatorsSuite:
    """Sürüm 3 Gösterge Doğrulama Kabul Kapısı test paketi."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 7/7 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        now = datetime.now(UTC)

        result: IndicatorsVerificationResult = gatekeeper.verify_release_three_indicators(now=now)

        assert result.status == ReleaseThreeIndGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 7
        assert all(c.passed for c in result.checks)
        assert RELEASE_THREE_INDICATORS_STATUS.get() == 1.0
        assert "7/7 adım başarılı" in result.summary
        assert result.evaluated_at == now

    def test_rsi_wilder_precision_check(self) -> None:
        """J. Welles Wilder 1978 14-günlük referans veri setinin bağımsız denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_rsi_wilder_benchmark()

        assert item.check_id == "REL3-IND-01-RSI-WILDER"
        assert item.passed is True
        assert "Wilder Day 15 RSI=" in item.details
        assert "bağımsız EWM azami sapma=" in item.details

    def test_atr_true_range_benchmark_check(self) -> None:
        """Bilinen High-Low-Close veri setiyle True Range ve ATR analitik denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_atr_true_range_benchmark()

        assert item.check_id == "REL3-IND-02-ATR-BENCHMARK"
        assert item.passed is True
        assert "Bar 3 ATR=" in item.details
        assert "0.7033" in item.details

    def test_moving_averages_algebraic_check(self) -> None:
        """Cebirsel kapalı formül ile SMA, EMA ve Hacim SMA denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_moving_averages_algebraic()

        assert item.check_id == "REL3-IND-03-SMA-EMA-ALGEBRAIC"
        assert item.passed is True
        assert "SMA(4) idx3=13.0" in item.details
        assert "EMA(3) idx2=12.5" in item.details
        assert "Volume SMA(3) idx2=2000.0" in item.details

    def test_macd_step_by_step_check(self) -> None:
        """Adım adım EWM hesaplamasıyla MACD hattı, sinyal hattı ve histogram denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_macd_step_by_step()

        assert item.check_id == "REL3-IND-04-MACD-STEP-BY-STEP"
        assert item.passed is True
        assert "MACD hattı azami sapma=" in item.details
        assert "sinyal hattı sapma=" in item.details
        assert "histogram sapma=" in item.details

    def test_historical_volatility_check(self) -> None:
        """Log-getiri standart sapması ve yıllıklandırılmış volatilite denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_historical_volatility()

        assert item.check_id == "REL3-IND-05-VOLATILITY-BENCHMARK"
        assert item.passed is True
        assert "Yıllıklandırılmamış oynaklık sapma=" in item.details
        assert "Yıllıklandırılmış oynaklık" in item.details

    def test_edge_cases_and_robustness_check(self) -> None:
        """Sıfır volatilite serisi, sabit fiyat RSI ve geçersiz parametre koruma denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_edge_cases_and_robustness()

        assert item.check_id == "REL3-IND-06-EDGE-CASES-ROBUSTNESS"
        assert item.passed is True
        assert "Sabit fiyat RSI=50.0" in item.details
        assert "Sabit volatilite=0.0" in item.details
        assert "İstisnalar: SMA=True" in item.details

    def test_pipeline_immutability_and_summary_check(self) -> None:
        """Pipeline immutability ve get_latest_indicator_summary veri uyum denetimi."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        item: IndicatorVerificationCheckItem = gatekeeper._check_pipeline_immutability_and_summary()

        assert item.check_id == "REL3-IND-07-PIPELINE-IMMUTABILITY"
        assert item.passed is True
        assert "Immutability=True" in item.details
        assert "Sütunlar=True" in item.details
        assert "Özet=True" in item.details

    def test_generate_markdown_report(self, tmp_path: Path) -> None:
        """Rapor oluşturma ve diske yazma sürecini test eder."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()
        result = gatekeeper.verify_release_three_indicators()

        report_file = tmp_path / "test_report.md"
        report_content = generate_markdown_report(result, report_path=report_file)

        assert report_file.exists()
        assert "Sürüm 3 (Klasik Analiz ve Uyarı)" in report_content
        assert "REL3-IND-01-RSI-WILDER" in report_content
        assert "REL3-IND-07-PIPELINE-IMMUTABILITY" in report_content
        assert "PASS" in report_content
        assert "J. Welles Wilder Jr." in report_content

    def test_gatekeeper_failure_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kontrol başarısız olduğunda kapı FAIL vermeli ve telemetri güncellenmelidir."""
        gatekeeper = ReleaseThreeIndicatorsGatekeeper()

        def mock_failed_check() -> IndicatorVerificationCheckItem:
            return IndicatorVerificationCheckItem(
                check_id="REL3-IND-MOCK-FAIL",
                name="Mock Başarısız Kontrol",
                passed=False,
                details="Kasıtlı test hatası",
            )

        monkeypatch.setattr(gatekeeper, "_check_rsi_wilder_benchmark", mock_failed_check)

        result = gatekeeper.verify_release_three_indicators()

        assert result.status == ReleaseThreeIndGateStatus.FAIL
        assert result.passed is False
        assert RELEASE_THREE_INDICATORS_STATUS.get() == 0.0
        assert "BAŞARISIZ" in result.summary
        assert "1/7 adım başarısız" in result.summary
