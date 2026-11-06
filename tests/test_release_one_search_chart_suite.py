"""Sürüm 1 Hisse Arama ve Geçmiş Grafik Doğrulama Kapısı Test Paketi."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stock_analysis.api.schemas import CandleItem
from stock_analysis.release.release_one_search_chart import (
    RELEASE_ONE_SEARCH_CHART_STATUS,
    ReleaseOneSCGateStatus,
    ReleaseOneSearchChartGatekeeper,
    SearchChartVerificationResult,
)


class TestReleaseOneSearchChartSuite:
    """Sürüm 1 Hisse Arama ve Geçmiş Grafik Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli, 6/6 kontrol başarılı olmalıdır."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()
        now = datetime.now(UTC)

        result: SearchChartVerificationResult = gatekeeper.verify_release_one_search_and_chart(
            sample_symbols=["THYAO", "ASELS", "GARAN", "KCHOL"],
            now=now,
        )

        assert result.status == ReleaseOneSCGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_ONE_SEARCH_CHART_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_search_symbols_exact_prefix_and_title(self) -> None:
        """Sembol kodu, önek ve şirket unvanı bazlı arama doğruluğunu test eder."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()

        # 1. Tam sembol araması
        res_thyao = gatekeeper.search_symbols(query="THYAO")
        assert len(res_thyao) > 0
        assert res_thyao[0].symbol == "THYAO"

        # 2. Önek araması
        res_prefix = gatekeeper.search_symbols(query="gar")
        assert any(r.symbol == "GARAN" for r in res_prefix)

        # 3. Unvan araması
        res_title = gatekeeper.search_symbols(query="Aselsan")
        assert any(r.symbol == "ASELS" for r in res_title)

        # 4. Bilinmeyen sembol (güvenli boş liste)
        res_none = gatekeeper.search_symbols(query="UNKNOWN_CODE_999")
        assert res_none == []

        # 5. Sayfalama desteği
        res_paged = gatekeeper.search_symbols(query="", page=1, page_size=3)
        assert len(res_paged) == 3

    def test_search_security_sanitization(self) -> None:
        """Zararlı XSS, SQL injection ve aşırı uzun girdilerin güvenli temizlenmesini test eder."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()

        # XSS temizliği
        clean_xss = gatekeeper.sanitize_search_query("<script>alert(1)</script>THYAO")
        assert "<script>" not in clean_xss.lower()
        assert "thyao" in clean_xss.lower()

        # SQL Injection temizliği
        clean_sqli = gatekeeper.sanitize_search_query("THYAO'; DROP TABLE instruments; --")
        assert "drop table" not in clean_sqli.lower()
        assert "--" not in clean_sqli

        # Uzunluk sınırlaması (maks 64 karakter)
        long_query = "A" * 120
        clean_long = gatekeeper.sanitize_search_query(long_query)
        assert len(clean_long) <= 64

    def test_chart_series_ohlcv_integrity(self) -> None:
        """Geçmiş grafik verilerinin OHLCV ve kronoloji tutarlılığını test eder."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()
        candles = gatekeeper.generate_synthetic_candles("THYAO", total_days=90)

        # Doğal seride tutarlılık tam olmalı
        metrics = gatekeeper.validate_chart_series("THYAO", candles)
        assert metrics.is_chronological is True
        assert metrics.is_ohlcv_consistent is True
        assert metrics.raw_points_count > 0

        # Yapay olarak bozulmuş seriyi tespit edebilmeli (High < Low anomalisi)
        corrupted_candles = list(candles)
        corrupted_candles[5] = CandleItem(
            date=corrupted_candles[5].date,
            open=100.0,
            high=90.0,  # High < Open / Low tutarsızlığı
            low=110.0,
            close=95.0,
            volume=5000.0,
            turnover_try=475000.0,
        )
        corrupted_metrics = gatekeeper.validate_chart_series("THYAO", corrupted_candles)
        assert corrupted_metrics.is_ohlcv_consistent is False

    def test_chart_downsampling_and_max_points(self) -> None:
        """İstemci performansını optimize eden aşağı örnekleme fonksiyonlarını test eder."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()
        candles = gatekeeper.generate_synthetic_candles("ASELS", total_days=365)
        raw_count = len(candles)

        # 1. Bucket örnekleme ile max_points=30
        metrics_bucket = gatekeeper.validate_chart_series(
            "ASELS", candles, downsample="bucket", max_points=30
        )
        assert metrics_bucket.rendered_points_count <= 30
        assert metrics_bucket.raw_points_count == raw_count

        # 2. Haftalık örnekleme
        metrics_weekly = gatekeeper.validate_chart_series("ASELS", candles, downsample="weekly")
        assert 40 <= metrics_weekly.rendered_points_count <= 54

        # 3. Aylık örnekleme
        metrics_monthly = gatekeeper.validate_chart_series("ASELS", candles, downsample="monthly")
        assert 10 <= metrics_monthly.rendered_points_count <= 14

    def test_generate_audit_report_file_generation(self, tmp_path: Path) -> None:
        """Resmi denetim raporunun üretilip dosyaya kaydedilmesini test eder."""
        gatekeeper = ReleaseOneSearchChartGatekeeper()
        result = gatekeeper.verify_release_one_search_and_chart()

        report_file = tmp_path / "RELEASE_1_SEARCH_CHART_REPORT.md"
        content = gatekeeper.generate_audit_report(result, target_file=report_file)

        assert report_file.exists()
        assert "Sürüm 1 — Hisse Arama ve Geçmiş Grafik Doğrulama Raporu" in content
        assert "REL1-SC-01-SYMBOL-SEARCH-MATCHING" in content
        assert "✅ PASS" in content
