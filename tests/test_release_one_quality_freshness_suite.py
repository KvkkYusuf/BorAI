"""Sürüm 1 Veri Kalitesi ve Güncellik Durumu Gösterimi Test Paketi."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stock_analysis.pipeline.validator import (
    DataIntegrityIssue,
    DataIntegrityReport,
    DataIssueType,
    IssueSeverity,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord
from stock_analysis.release.release_one_quality_freshness import (
    RELEASE_ONE_QUALITY_FRESHNESS_STATUS,
    DataQualityUiBadge,
    QualityFreshnessVerificationResult,
    ReleaseOneDataQualityFreshnessGatekeeper,
    ReleaseOneQFGateStatus,
    SymbolDataQualitySummary,
)


def _make_bar(
    symbol: str,
    bar_date: date,
    session_dt: datetime,
    close: float = 100.0,
    volume: int = 1000,
) -> MarketBarRecord:
    price = Decimal(str(close))
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=bar_date,
        timezone="Europe/Istanbul",
        session_time_utc=session_dt,
        currency="TRY",
        data_status=DataStatus.VERIFIED,
        raw_open=price - Decimal("1.00"),
        raw_high=price + Decimal("2.00"),
        raw_low=price - Decimal("2.00"),
        raw_close=price,
        adj_open=price - Decimal("1.00"),
        adj_high=price + Decimal("2.00"),
        adj_low=price - Decimal("2.00"),
        adj_close=price,
        volume=volume,
        turnover_try=price * volume,
        source_name="licensed_feed",
        is_verified=True,
        ingested_at=session_dt,
    )


class TestReleaseOneDataQualityFreshness:
    """Sürüm 1 Veri Kalitesi ve Güncellik Kapısı testleri."""

    def test_gatekeeper_full_verification_pass(self) -> None:
        """Tüm kabul kriterleri sağlandığında kapı PASS vermeli ve 6 kontrol de geçmeli."""
        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        now = datetime.now(UTC)

        result: QualityFreshnessVerificationResult = (
            gatekeeper.verify_release_one_data_quality_and_freshness(
                symbols=["THYAO", "ASELS", "GARAN"],
                now=now,
            )
        )

        assert result.status == ReleaseOneQFGateStatus.PASS
        assert result.passed is True
        assert len(result.checks) == 6
        assert all(c.passed for c in result.checks)
        assert RELEASE_ONE_QUALITY_FRESHNESS_STATUS.get() == 1.0
        assert "6/6 adım başarılı" in result.summary

    def test_symbol_display_summary_fresh_and_clean(self) -> None:
        """Temiz ve güncel veri çubukları EXCELLENT rozetine sahip olmalı."""
        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        now = datetime.now(UTC)

        bars = [
            _make_bar("THYAO", (now - timedelta(days=2)).date(), now - timedelta(days=2), 250.0),
            _make_bar("THYAO", (now - timedelta(days=1)).date(), now - timedelta(days=1), 255.0),
        ]

        summary: SymbolDataQualitySummary = gatekeeper.build_symbol_display_summary(
            symbol="THYAO",
            bars=bars,
            now=now,
        )

        assert summary.symbol == "THYAO"
        assert summary.total_bars_evaluated == 2
        assert summary.invalid_bars_count == 0
        assert summary.anomaly_rate_pct == 0.0
        assert summary.quality_badge == DataQualityUiBadge.EXCELLENT
        assert summary.is_acceptable_for_display is True
        assert "bütünlüğü tam doğrulandı" in summary.display_notice
        assert summary.freshness_info.freshness_label != ""
        assert summary.freshness_info.display_badge != ""

    def test_symbol_display_summary_empty_bars_fallback(self) -> None:
        """Veri bulunmadığında çökmemeli ve INSUFFICIENT dönmeli."""
        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        now = datetime.now(UTC)

        summary = gatekeeper.build_symbol_display_summary("EMPTY_SYM", [], now=now)

        assert summary.symbol == "EMPTY_SYM"
        assert summary.total_bars_evaluated == 0
        assert summary.quality_badge == DataQualityUiBadge.INSUFFICIENT
        assert summary.is_acceptable_for_display is False
        assert summary.freshness_info.display_badge == "VERİ YOK"

    def test_symbol_display_summary_anomaly_threshold_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hatalı çubuk tespitinde anomali oranı eşiği aşıldığında ANOMALY_WARNING dönmeli."""
        from stock_analysis.release import release_one_quality_freshness

        def mock_validate(bars: object) -> DataIntegrityReport:
            return DataIntegrityReport(
                symbol="ASELS",
                total_bars=1,
                is_valid=False,
                issues=[
                    DataIntegrityIssue(
                        issue_type=DataIssueType.INCONSISTENT_OHLC,
                        severity=IssueSeverity.ERROR,
                        trade_date=date(2026, 1, 1),
                        description="Corrupted bar",
                    )
                ],
            )

        monkeypatch.setattr(
            release_one_quality_freshness, "validate_market_data_series", mock_validate
        )

        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        now = datetime.now(UTC)
        bars = [_make_bar("ASELS", now.date(), now, 80.0)]

        summary = gatekeeper.build_symbol_display_summary("ASELS", bars, now=now)

        assert summary.anomaly_rate_pct == 100.0
        assert summary.quality_badge == DataQualityUiBadge.ANOMALY_WARNING
        assert summary.is_acceptable_for_display is False
        assert "Dikkat: Veri anomalisi oranı yüksek" in summary.display_notice

    def test_pydantic_schema_serialization(self) -> None:
        """SymbolDataQualitySummary çıktısı JSON / dict formatına eksiksiz serileştirilebilmeli."""
        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        now = datetime.now(UTC)
        bars = [_make_bar("KCHOL", now.date(), now, 180.0)]

        summary = gatekeeper.build_symbol_display_summary("KCHOL", bars, now=now)
        dumped = summary.model_dump()

        assert dumped["symbol"] == "KCHOL"
        assert "freshness_info" in dumped
        assert dumped["quality_badge"] == DataQualityUiBadge.EXCELLENT.value
        assert dumped["total_bars_evaluated"] == 1
        assert "evaluated_at" in dumped

    def test_gatekeeper_to_dict_format(self) -> None:
        """VerificationResult.to_dict API formatını eksiksiz sağlamalı."""
        gatekeeper = ReleaseOneDataQualityFreshnessGatekeeper()
        res = gatekeeper.verify_release_one_data_quality_and_freshness()
        data = res.to_dict()

        assert data["status"] == "PASS"
        assert data["passed"] is True
        assert data["total_checks"] == 6
        assert len(data["checks"]) == 6
        assert "timestamp" in data
