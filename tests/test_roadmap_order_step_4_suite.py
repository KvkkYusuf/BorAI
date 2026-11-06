"""FAZ-18 Yol Haritası Uygulama Sırası — Adım 4 test paketi.

Lisanslı veri adaptörü ve kalite kontrolleri doğrulama paketi.
Test kapsamı:
- RoadmapStepFourGatekeeper 6/6 tam uyum doğrulaması.
- Piyasa verisi sağlayıcı adaptörleri (BaseMarketDataProvider, LicensedEODDataProvider, vb.).
- BIST EOD kayıt şeması (MarketBarRecord, symbol, timestamp, OHLCV, TRY).
- Veri kalitesi ve anomali tespiti (validate_market_data_series, OHLC mantıksal uyumu).
- Borsa İstanbul borsa takvimi (BISTCalendar, resmi tatiller, hafta sonu, yarım seanslar).
- Değişmez ham veri saklama ve idempotent alım (ImmutableRawStore, MarketDataPipelineService).
- Veri kullanım politikası, web kazıma yasağı (ScrapingGuard, RedistributionGuard).
- Zorunlu SPK yasal uyarı şablonu ve yasaklı terim denetimi.
- Prometheus telemetri metrikleri ve Markdown denetim raporu üretimi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_analysis.release.roadmap_order_gatekeeper import (
    DEFAULT_ROADMAP_STEP4_DISCLAIMER,
    ROADMAP_STEP4_CHECKS_TOTAL,
    ROADMAP_STEP4_PASSED_TOTAL,
    ROADMAP_STEP4_STATUS,
    RoadmapStepFourGatekeeper,
    RoadmapStepFourStatus,
    generate_roadmap_step4_report,
)


@pytest.fixture
def workspace_root() -> Path:
    """Proje kök dizinini döndürür."""
    return Path(__file__).resolve().parent.parent


def test_roadmap_step4_gatekeeper_full_pass(workspace_root: Path) -> None:
    """Tüm Adım 4 kontrollerinin 6/6 PASS olarak geçtiğini doğrular."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFourStatus.PASS
    assert result.total_checks == 6
    assert result.passed_checks == 6
    assert result.failed_checks == 0
    assert len(result.checks) == 6

    for check in result.checks:
        assert check.passed is True
        assert len(check.evidence) > 10


def test_roadmap_step4_provider_adapters_validation(workspace_root: Path) -> None:
    """Piyasa verisi sağlayıcı adaptörlerini ve soyut arayüzleri denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S4-01-MARKET-DATA-PROVIDER-ADAPTERS"
    )
    assert check.passed is True
    assert check.details["has_base"] is True
    assert check.details["has_licensed"] is True
    assert check.details["has_synthetic"] is True
    assert check.details["has_cached"] is True
    assert check.details["has_factory"] is True
    assert check.details["has_abstract_methods"] is True


def test_roadmap_step4_bist_eod_schema_and_timing_validation(
    workspace_root: Path,
) -> None:
    """BIST EOD kayıt şeması, zaman dilimi ve güncellik kategorizasyonunu denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S4-02-BIST-EOD-RECORD-SCHEMA-AND-TIMING"
    )
    assert check.passed is True
    assert check.details["has_base"] is True
    assert check.details["has_freshness"] is True
    assert check.details["has_bar_fields"] is True
    assert check.details["has_try_currency"] is True
    assert check.details["has_freshness_logic"] is True


def test_roadmap_step4_data_quality_controls_validation(workspace_root: Path) -> None:
    """Veri kalitesi ve anomali tespiti doğrulama kontrollerini denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S4-03-DATA-QUALITY-AND-ANOMALY-CONTROLS"
    )
    assert check.passed is True
    assert check.details["has_validator"] is True
    assert check.details["has_validator_func"] is True
    assert check.details["has_price_checks"] is True
    assert check.details["has_anomaly_handling"] is True


def test_roadmap_step4_bist_calendar_rules_validation(workspace_root: Path) -> None:
    """Borsa İstanbul takvimi, tatiller ve seans kurallarını denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c for c in result.checks if c.check_id == "ROADMAP-S4-04-BIST-CALENDAR-AND-SESSION-RULES"
    )
    assert check.passed is True
    assert check.details["has_calendar"] is True
    assert check.details["has_calendar_class"] is True
    assert check.details["has_weekend_check"] is True
    assert check.details["has_holiday_handling"] is True


def test_roadmap_step4_immutable_raw_store_validation(workspace_root: Path) -> None:
    """Değişmez ham veri deposu ve idempotent alım mekanizmasını denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S4-05-IMMUTABLE-RAW-STORE-AND-IDEMPOTENCY"
    )
    assert check.passed is True
    assert check.details["has_storage"] is True
    assert check.details["has_immutable_store"] is True
    assert check.details["has_pipeline_service"] is True
    assert check.details["has_idempotency"] is True


def test_roadmap_step4_data_usage_policy_validation(workspace_root: Path) -> None:
    """Veri kullanım politikası, web kazıma yasağı ve sözleşme korumasını denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    check = next(
        c
        for c in result.checks
        if c.check_id == "ROADMAP-S4-06-DATA-USAGE-POLICY-AND-SCRAPING-PROHIBITION"
    )
    assert check.passed is True
    assert check.details["has_licensing"] is True
    assert check.details["has_scraping_guard"] is True
    assert check.details["has_redistribution_guard"] is True
    assert check.details["has_usage_policy"] is True


def test_roadmap_step4_report_generation(workspace_root: Path) -> None:
    """Adım 4 için Markdown formatında denetim raporu üretimini test eder."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    report = generate_roadmap_step4_report(result)
    assert "# Yol Haritası Uygulama Sırası — Adım 4" in report
    assert "✅ PASS" in report
    assert "ROADMAP-S4-01-MARKET-DATA-PROVIDER-ADAPTERS" in report
    assert "ROADMAP-S4-06-DATA-USAGE-POLICY-AND-SCRAPING-PROHIBITION" in report
    assert DEFAULT_ROADMAP_STEP4_DISCLAIMER in report
    assert "Yasal Uyarı ve Sorumluluk Reddi" in report


def test_roadmap_step4_result_to_dict_and_disclaimer(workspace_root: Path) -> None:
    """Sonucun to_dict metodunu ve yasal çekince metnini denetler."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    data = result.to_dict()
    assert data["status"] == "PASS"
    assert data["total_checks"] == 6
    assert data["passed_checks"] == 6
    assert data["failed_checks"] == 0
    assert len(data["checks"]) == 6
    assert data["disclaimer"] == DEFAULT_ROADMAP_STEP4_DISCLAIMER


def test_roadmap_step4_gatekeeper_failure_case(tmp_path: Path) -> None:
    """Eksik veya boş dizin durumunda Adım 4 kapısının FAIL verdiğini doğrular."""
    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=tmp_path)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFourStatus.FAIL
    assert result.failed_checks > 0
    assert result.passed_checks < 6


def test_roadmap_step4_prometheus_metrics(workspace_root: Path) -> None:
    """Prometheus telemetri metriklerinin güncellendiğini doğrular."""
    before_checks = ROADMAP_STEP4_CHECKS_TOTAL.get()
    before_passed = ROADMAP_STEP4_PASSED_TOTAL.get()

    gatekeeper = RoadmapStepFourGatekeeper(workspace_root=workspace_root)
    result = gatekeeper.evaluate()

    assert result.status == RoadmapStepFourStatus.PASS
    assert ROADMAP_STEP4_STATUS.get() == 1.0
    assert ROADMAP_STEP4_CHECKS_TOTAL.get() >= before_checks + 6
    assert ROADMAP_STEP4_PASSED_TOTAL.get() >= before_passed + 6
