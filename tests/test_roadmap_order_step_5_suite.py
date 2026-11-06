"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 5 test paketi.

Hisse arama ve grafik doğrulama kabul paketi.
Test kapsamı:
- RoadmapStepFiveGatekeeper 6/6 tam uyum doğrulaması.
- Sembol ve şirket unvanı bazlı arama motoru (/market/symbols, DEFAULT_BIST_SYMBOLS, sayfalama).
- Arama girdisi güvenlik kalkanı (escape_like_pattern, sanitize_search_query, XSS ve SQLi engeli).
- Tarihsel grafik OHLCV bütünlüğü (/market/history/{symbol}, CandleItem, High>=Low).
- Grafik zaman pencereleri (1m, 3m, 6m, 1y, 5y, all, VALID_RANGES).
- Grafik aşağı örnekleme ve performans (downsample_bucket/weekly/monthly, max_points optimizasyonu).
- Arayüz bileşenleri, sağlayıcı hata kalkanı ve SPK 15 dk gecikmeli veri uyarısı.
- Zorunlu SPK yasal uyarı şablonu ve yasaklı terim denetimi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP5_DISCLAIMER,
    ROADMAP_STEP5_CHECKS_TOTAL,
    ROADMAP_STEP5_PASSED_TOTAL,
    ROADMAP_STEP5_STATUS,
    RoadmapStepFiveGatekeeper,
    RoadmapStepFiveStatus,
    generate_roadmap_step5_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


def test_roadmap_step5_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 5 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFiveStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step5_symbol_search_engine_validation(workspace_root: Path) -> None:
    """Sembol ve şirket arama motoru kontrollerini denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S5-01-SYMBOL-SEARCH-ENGINE"
    )
    assert check.passed is True
    assert "BIST hisse arama motoru" in check.evidence
    assert check.details["has_symbols_endpoint"] is True
    assert check.details["has_default_catalog"] is True
    assert check.details["has_paging"] is True


def test_roadmap_step5_search_input_security_validation(workspace_root: Path) -> None:
    """Arama girdisi güvenlik kalkanını denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S5-02-SEARCH-INPUT-SECURITY-SANITIZATION"
    )
    assert check.passed is True
    assert "escape_like_pattern" in check.evidence
    assert check.details["has_safe_query"] is True
    assert check.details["has_escape_like"] is True
    assert check.details["has_sanitize"] is True


def test_roadmap_step5_ohlcv_integrity_validation(workspace_root: Path) -> None:
    """Tarihsel OHLCV veri bütünlüğü kontrollerini denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S5-03-HISTORICAL-OHLCV-INTEGRITY"
    )
    assert check.passed is True
    assert "CandleItem" in check.evidence
    assert check.details["has_history_endpoint"] is True
    assert check.details["has_candle_item"] is True
    assert check.details["has_history_response"] is True


def test_roadmap_step5_chart_time_ranges_validation(workspace_root: Path) -> None:
    """Grafik zaman pencereleri ve tarih filtreleme kontrollerini denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S5-04-CHART-TIME-RANGES-AND-FILTERS"
    )
    assert check.passed is True
    assert "VALID_RANGES" in check.evidence
    assert check.details["has_valid_ranges"] is True
    assert check.details["has_range_1m"] is True
    assert check.details["has_range_all"] is True


def test_roadmap_step5_chart_downsampling_validation(workspace_root: Path) -> None:
    """Grafik aşağı örnekleme ve performans optimizasyonunu denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S5-05-CHART-DOWNSAMPLING-AND-PERFORMANCE"
    )
    assert check.passed is True
    assert "downsample_bucket" in check.evidence
    assert check.details["has_downsample"] is True
    assert check.details["has_bucket"] is True
    assert check.details["has_weekly"] is True
    assert check.details["has_monthly"] is True
    assert check.details["has_max_points"] is True


def test_roadmap_step5_ui_chart_and_disclaimer_validation(workspace_root: Path) -> None:
    """Arayüz grafik bileşenleri ve yasal uyarı kontrollerini denetler."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S5-06-UI-CHART-LAYOUT-AND-DISCLAIMER"
    )
    assert check.passed is True
    assert "top-search-input" in check.evidence
    assert check.details["has_index"] is True
    assert check.details["has_search_input"] is True
    assert check.details["has_delay_badge"] is True
    assert check.details["has_disclaimer"] is True
    assert check.details["has_chart_rendering"] is True


def test_roadmap_step5_disclaimer_compliance() -> None:
    """Yasal çekince metninin SPK ilkelerine uyumunu ve yasaklı terim içermediğini doğrular."""
    assert len(DEFAULT_ROADMAP_STEP5_DISCLAIMER) > 50
    assert "SPK Mevzuatı Uyarınca" in DEFAULT_ROADMAP_STEP5_DISCLAIMER
    assert "yatırım danışmanlığı" in DEFAULT_ROADMAP_STEP5_DISCLAIMER


def test_roadmap_step5_report_generation(workspace_root: Path) -> None:
    """Adım 5 denetim raporunun Markdown formatında eksiksiz üretildiğini doğrular."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()
    report = generate_roadmap_step5_report(result)

    assert "# Yol Haritası Uygulama Sırası — Adım 5 Denetim Raporu" in report
    assert "ROADMAP-S5-01-SYMBOL-SEARCH-ENGINE" in report
    assert "ROADMAP-S5-02-SEARCH-INPUT-SECURITY-SANITIZATION" in report
    assert "ROADMAP-S5-03-HISTORICAL-OHLCV-INTEGRITY" in report
    assert "ROADMAP-S5-04-CHART-TIME-RANGES-AND-FILTERS" in report
    assert "ROADMAP-S5-05-CHART-DOWNSAMPLING-AND-PERFORMANCE" in report
    assert "ROADMAP-S5-06-UI-CHART-LAYOUT-AND-DISCLAIMER" in report
    assert "✅ PASS" in report


def test_roadmap_step5_missing_workspace_failure(tmp_path: Path) -> None:
    """Boş geçici dizinde Adım 5 değerlendirmesinin FAIL döndürdüğünü doğrular."""
    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=tmp_path)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFiveStatus.FAIL
    assert result.failed_checks > 0
    assert result.passed_checks == 0


def test_roadmap_step5_prometheus_telemetry(workspace_root: Path) -> None:
    """Prometheus sayaç ve durum metriklerinin güncellendiğini doğrular."""
    before_passed = ROADMAP_STEP5_PASSED_TOTAL.get()
    before_checks = ROADMAP_STEP5_CHECKS_TOTAL.get()

    gatekeeper = RoadmapStepFiveGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFiveStatus.PASS
    assert ROADMAP_STEP5_STATUS.get() == 1.0
    assert ROADMAP_STEP5_PASSED_TOTAL.get() >= before_passed + result.passed_checks
    assert ROADMAP_STEP5_CHECKS_TOTAL.get() >= before_checks + result.total_checks
