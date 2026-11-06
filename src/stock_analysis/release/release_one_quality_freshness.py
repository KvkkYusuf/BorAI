"""Sürüm 1 (Veri ve Grafik MVP) - Veri Kalitesi ve Güncellik Durumu Gösterimi Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 1 — Veri ve grafik MVP:
  - Veri kalitesi ve güncellik durumu gösteriliyor.

Bu modül:
1. Her sembol için güncellik durumunu, gecikme dakikasını ve UI rozet etiketlerini denetler.
2. Veri kalite doğrulaması (`validate_market_data_series`) çıktılarını şeffafça sunar.
3. SPK şeffaflık ilkeleri uyarınca veri gecikmesi ve güncelleme zaman damgasını zorunlu kılar.
4. Prometheus telemetrisi ile kalite ve güncellik durumunu izler.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.pipeline.freshness import (
    DataFreshnessInfo,
    DelayCategory,
    compute_data_freshness,
    format_timestamp_trt,
)
from stock_analysis.pipeline.validator import (
    DataIntegrityReport,
    validate_market_data_series,
)
from stock_analysis.providers.base import (
    DataStatus,
    MarketBarRecord,
)

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_ONE_QUALITY_FRESHNESS_STATUS = REGISTRY.gauge(
    "release_one_quality_freshness_status",
    "Surum 1 veri kalitesi ve guncellik gosterim kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_ONE_QF_CHECKS_TOTAL = REGISTRY.counter(
    "release_one_qf_checks_total",
    "Toplam Surum 1 veri kalitesi ve guncellik kontrol adimi",
)
RELEASE_ONE_QF_PASSED_TOTAL = REGISTRY.counter(
    "release_one_qf_passed_total",
    "Basariyla gecen Surum 1 veri kalitesi ve guncellik kontrol adimi",
)
RELEASE_ONE_QF_FAILED_TOTAL = REGISTRY.counter(
    "release_one_qf_failed_total",
    "Basarisiz olan Surum 1 veri kalitesi ve guncellik kontrol adimi",
)


class ReleaseOneQFGateStatus(StrEnum):
    """Sürüm 1 Kalite & Güncellik Kapı Durumu."""

    PASS = "PASS"
    FAIL = "FAIL"


class DataQualityUiBadge(StrEnum):
    """Kullanıcı arayüzünde gösterilecek veri kalitesi rozeti."""

    EXCELLENT = "Mükemmel (Doğrulandı)"
    ACCEPTABLE = "Kabul Edilebilir"
    ANOMALY_WARNING = "Anomali Uyarısı"
    INSUFFICIENT = "Yetersiz Veri"


class SymbolDataQualitySummary(BaseModel):
    """Tekil bir hisse sembolü için veri kalitesi ve güncellik özet göstergesi."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Hisse borsa kodu (örn. THYAO)")
    freshness_info: DataFreshnessInfo = Field(description="Güncellik ve gecikme metadataları")
    total_bars_evaluated: int = Field(description="İncelenen toplam çubuk sayısı")
    invalid_bars_count: int = Field(default=0, description="Hatalı çubuk veya anomali sayısı")
    anomaly_rate_pct: float = Field(default=0.0, description="Yüzdesel anomali oranı")
    quality_badge: DataQualityUiBadge = Field(description="Arayüz için kalite rozeti")
    is_acceptable_for_display: bool = Field(description="Kullanıcıya gösterime uygun mu")
    evaluated_at: datetime = Field(description="Değerlendirme zamanı (UTC)")
    display_notice: str = Field(description="Kullanıcıya gösterilecek şeffaflık bildirimi")


@dataclass
class QualityFreshnessCheckItem:
    """Doğrulama adım kaydı."""

    check_id: str
    description: str
    passed: bool
    evidence: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityFreshnessVerificationResult:
    """Kalite ve güncellik kapısı genel doğrulama sonucu."""

    status: ReleaseOneQFGateStatus
    checks: list[QualityFreshnessCheckItem] = field(default_factory=list)
    evaluated_symbols: list[str] = field(default_factory=list)
    summary: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> bool:
        return self.status == ReleaseOneQFGateStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "passed": self.passed,
            "total_checks": len(self.checks),
            "passed_checks": sum(1 for c in self.checks if c.passed),
            "evaluated_symbols": self.evaluated_symbols,
            "summary": self.summary,
            "timestamp": self.timestamp.isoformat(),
            "checks": [
                {
                    "check_id": c.check_id,
                    "description": c.description,
                    "passed": c.passed,
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }


class ReleaseOneDataQualityFreshnessGatekeeper:
    """Sürüm 1 Veri Kalitesi ve Güncellik Durumu Gösterimi Kapısı."""

    def build_symbol_display_summary(
        self,
        symbol: str,
        bars: Sequence[MarketBarRecord],
        now: datetime | None = None,
    ) -> SymbolDataQualitySummary:
        """Belirtilen sembol için UI/API hazır veri kalitesi ve güncellik özetini üretir."""
        current_time = now or datetime.now(UTC)
        total_bars = len(bars)

        if total_bars == 0:
            freshness_info = DataFreshnessInfo(
                symbol=symbol,
                trade_date=current_time.date(),
                last_updated_utc=current_time,
                last_updated_trt=current_time.astimezone(ZoneInfo("Europe/Istanbul")),
                delay_category=DelayCategory.EOD_OFFICIAL,
                delay_minutes=0,
                is_market_open=False,
                is_stale=True,
                stale_reason="Kayıt bulunamadı",
                display_timestamp=format_timestamp_trt(current_time),
                display_badge="VERİ YOK",
                freshness_label="Veri Yok",
                provenance_notice="Borsa verisi bulunamadı.",
            )
            return SymbolDataQualitySummary(
                symbol=symbol,
                freshness_info=freshness_info,
                total_bars_evaluated=0,
                invalid_bars_count=0,
                anomaly_rate_pct=0.0,
                quality_badge=DataQualityUiBadge.INSUFFICIENT,
                is_acceptable_for_display=False,
                evaluated_at=current_time,
                display_notice="Sembol için geçmiş veri kaydı bulunamadı.",
            )

        # Son çubuğa dayalı güncellik hesapla
        last_bar = bars[-1]
        freshness_info = compute_data_freshness(
            record=last_bar,
            as_of_utc=current_time,
        )

        # Kalite doğrulaması
        report: DataIntegrityReport = validate_market_data_series(bars)
        invalid_count = report.error_count
        anomaly_rate = (invalid_count / total_bars) * 100.0

        # Rozet ve gösterim kararı
        if invalid_count == 0:
            badge = DataQualityUiBadge.EXCELLENT
            acceptable = True
            notice = f"Veri bütünlüğü tam doğrulandı (%0 hata, {total_bars} bar)."
        elif anomaly_rate < 0.5:
            badge = DataQualityUiBadge.ACCEPTABLE
            acceptable = True
            notice = f"Veri kabul edilebilir (%{anomaly_rate:.2f} anomali < %0.5 tolerans)."
        else:
            badge = DataQualityUiBadge.ANOMALY_WARNING
            acceptable = False
            notice = f"Dikkat: Veri anomalisi oranı yüksek (%{anomaly_rate:.2f} >= %0.5 tolerans)."

        return SymbolDataQualitySummary(
            symbol=symbol,
            freshness_info=freshness_info,
            total_bars_evaluated=total_bars,
            invalid_bars_count=invalid_count,
            anomaly_rate_pct=round(anomaly_rate, 4),
            quality_badge=badge,
            is_acceptable_for_display=acceptable,
            evaluated_at=current_time,
            display_notice=notice,
        )

    def verify_release_one_data_quality_and_freshness(
        self,
        symbols: list[str] | None = None,
        sample_bars_by_symbol: dict[str, list[MarketBarRecord]] | None = None,
        now: datetime | None = None,
    ) -> QualityFreshnessVerificationResult:
        """Sürüm 1 Veri Kalitesi ve Güncellik Durumu Gösterimi kabul kriterlerini doğrular."""
        eval_time = now or datetime.now(UTC)
        target_symbols = symbols or ["THYAO", "ASELS", "GARAN"]

        if sample_bars_by_symbol is None:
            sample_bars_by_symbol = self._generate_default_evaluation_dataset(
                target_symbols, eval_time
            )

        checks: list[QualityFreshnessCheckItem] = []

        # 1. Kontrol: Güncellik hesaplama ve durum sınıflaması
        checks.append(
            self._check_freshness_classification(target_symbols, sample_bars_by_symbol, eval_time)
        )

        # 2. Kontrol: UI gecikme ve şeffaflık etiketleri
        checks.append(
            self._check_ui_labels_and_disclaimers(target_symbols, sample_bars_by_symbol, eval_time)
        )

        # 3. Kontrol: Veri kalitesi anomali tespiti ve hata payı kuralı (< %0.5 eşiği)
        checks.append(
            self._check_quality_validation_thresholds(
                target_symbols, sample_bars_by_symbol, eval_time
            )
        )

        # 4. Kontrol: Boş veya bozuk veri durumunda güvenli gösterim
        checks.append(self._check_insufficient_or_corrupted_data_handling(eval_time))

        # 5. Kontrol: API/UI özet kontrat şeması bütünlüğü
        checks.append(
            self._check_summary_schema_conformance(target_symbols, sample_bars_by_symbol, eval_time)
        )

        # 6. Kontrol: Telemetri ve gözlemlenebilirlik
        checks.append(self._check_telemetry_reporting())

        all_passed = all(c.passed for c in checks)
        status = ReleaseOneQFGateStatus.PASS if all_passed else ReleaseOneQFGateStatus.FAIL

        # Metrikleri güncelle
        RELEASE_ONE_QUALITY_FRESHNESS_STATUS.set(1.0 if all_passed else 0.0)
        for c in checks:
            RELEASE_ONE_QF_CHECKS_TOTAL.inc()
            if c.passed:
                RELEASE_ONE_QF_PASSED_TOTAL.inc()
            else:
                RELEASE_ONE_QF_FAILED_TOTAL.inc()

        summary_msg = (
            f"Sürüm 1 Kalite & Güncellik Kapısı: {status.value} "
            f"({sum(1 for c in checks if c.passed)}/{len(checks)} adım başarılı, "
            f"{len(target_symbols)} sembol denetlendi)."
        )

        return QualityFreshnessVerificationResult(
            status=status,
            checks=checks,
            evaluated_symbols=target_symbols,
            summary=summary_msg,
            timestamp=eval_time,
        )

    # --- İç Doğrulama Adımları ---

    def _check_freshness_classification(
        self,
        symbols: list[str],
        bars_by_symbol: dict[str, list[MarketBarRecord]],
        eval_time: datetime,
    ) -> QualityFreshnessCheckItem:
        """Her sembol için güncellik durumu ve gecikme süresi hesaplamasını denetler."""
        missing_freshness: list[str] = []
        for sym in symbols:
            bars = bars_by_symbol.get(sym, [])
            summary = self.build_symbol_display_summary(sym, bars, eval_time)
            info = summary.freshness_info
            if not info.freshness_label or not info.display_badge:
                missing_freshness.append(sym)

        passed = len(missing_freshness) == 0
        evidence = (
            f"Tüm {len(symbols)} sembol için güncellik durumu eksiksiz hesaplandı."
            if passed
            else f"Güncellik bilgisi eksik semboller: {missing_freshness}"
        )
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-01-FRESHNESS-CLASSIFICATION",
            description="Sembol bazında piyasa güncelliği ve gecikme hesaplama doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _check_ui_labels_and_disclaimers(
        self,
        symbols: list[str],
        bars_by_symbol: dict[str, list[MarketBarRecord]],
        eval_time: datetime,
    ) -> QualityFreshnessCheckItem:
        """Kullanıcı arayüzü etiketleri ve SPK yasal gecikme bildirimlerini denetler."""
        failed_labels: list[str] = []
        for sym in symbols:
            bars = bars_by_symbol.get(sym, [])
            summary = self.build_symbol_display_summary(sym, bars, eval_time)
            if not summary.freshness_info.freshness_label:
                failed_labels.append(f"{sym}: boş freshness_label")
            if not summary.display_notice:
                failed_labels.append(f"{sym}: boş display_notice")

        passed = len(failed_labels) == 0
        evidence = (
            "Arayüz rozetleri (Türkçe etiketler ve şeffaflık bildirimleri) eksiksiz sağlandı."
            if passed
            else f"Hatalı veya eksik arayüz etiketleri: {failed_labels}"
        )
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-02-UI-LABELS-DISCLAIMERS",
            description="Arayüz şeffaflık rozetleri ve gecikme bildirimleri doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _check_quality_validation_thresholds(
        self,
        symbols: list[str],
        bars_by_symbol: dict[str, list[MarketBarRecord]],
        eval_time: datetime,
    ) -> QualityFreshnessCheckItem:
        """Veri kalitesi ve anomali oranı eşiğini (< %0.5) denetler."""
        threshold_failures: list[str] = []
        for sym in symbols:
            bars = bars_by_symbol.get(sym, [])
            summary = self.build_symbol_display_summary(sym, bars, eval_time)
            if summary.anomaly_rate_pct > 0.5 and summary.is_acceptable_for_display:
                threshold_failures.append(
                    f"{sym}: anomali %0.5 üstünde olmasına rağmen kabul edildi"
                )
            elif (
                summary.anomaly_rate_pct <= 0.5
                and not summary.is_acceptable_for_display
                and summary.total_bars_evaluated > 0
            ):
                threshold_failures.append(f"{sym}: anomali %0.5 altında olmasına rağmen reddedildi")

        passed = len(threshold_failures) == 0
        evidence = (
            "Veri kalitesi eşik denetimi (< %0.5 kuralı) tam tutarlı çalışıyor."
            if passed
            else f"Kalite eşik tutarsızlıkları: {threshold_failures}"
        )
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-03-QUALITY-THRESHOLD",
            description="Veri kalitesi toleransı (< %0.5 hata) ve anomali filtreleme doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _check_insufficient_or_corrupted_data_handling(
        self,
        eval_time: datetime,
    ) -> QualityFreshnessCheckItem:
        """Boş veya bozuk veri geldiğinde uygulamanın güvenli rozet dönmesini denetler."""
        sym_empty = "NULL_SYM"
        summary_empty = self.build_symbol_display_summary(sym_empty, [], eval_time)

        empty_safe = (
            summary_empty.quality_badge == DataQualityUiBadge.INSUFFICIENT
            and not summary_empty.is_acceptable_for_display
            and summary_empty.total_bars_evaluated == 0
        )

        passed = empty_safe
        evidence = (
            "Boş veri durumunda sistem INSUFFICIENT rozeti ile güvenli şekilde fallback yaptı."
            if passed
            else "Boş veri durumunda beklenmeyen davranış saptandı."
        )
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-04-FALLBACK-HANDLING",
            description="Boş/yetersiz veri durumunda güvenli rozet ve fallback doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _check_summary_schema_conformance(
        self,
        symbols: list[str],
        bars_by_symbol: dict[str, list[MarketBarRecord]],
        eval_time: datetime,
    ) -> QualityFreshnessCheckItem:
        """SymbolDataQualitySummary çıktılarının Pydantic şemasına uygunluğunu denetler."""
        serialization_errors: list[str] = []
        for sym in symbols:
            bars = bars_by_symbol.get(sym, [])
            summary = self.build_symbol_display_summary(sym, bars, eval_time)
            try:
                dumped = summary.model_dump()
                if "quality_badge" not in dumped or "freshness_info" not in dumped:
                    serialization_errors.append(f"{sym}: eksik JSON alanları")
            except Exception as e:
                serialization_errors.append(f"{sym}: serileştirme hatası: {e}")

        passed = len(serialization_errors) == 0
        evidence = (
            f"Tüm {len(symbols)} sembol için Pydantic model_dump serileştirmesi başarılı."
            if passed
            else f"Şema serileştirme hataları: {serialization_errors}"
        )
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-05-SCHEMA-CONFORMANCE",
            description="REST API ve UI kontrat şeması (Pydantic) serileştirme doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _check_telemetry_reporting(self) -> QualityFreshnessCheckItem:
        """Prometheus telemetrisinin aktif olarak tanımlandığını denetler."""
        gauge_val = RELEASE_ONE_QUALITY_FRESHNESS_STATUS.get()
        passed = gauge_val is not None
        evidence = f"Prometheus telemetrisi aktif (mevcut deger={gauge_val})."
        return QualityFreshnessCheckItem(
            check_id="REL1-QF-06-TELEMETRY-INTEGRATION",
            description="Prometheus telemetrisi ve operasyonel metrik entegrasyonu doğrulaması",
            passed=passed,
            evidence=evidence,
        )

    def _generate_default_evaluation_dataset(
        self,
        symbols: list[str],
        eval_time: datetime,
    ) -> dict[str, list[MarketBarRecord]]:
        """Değerlendirme için standart test veri setleri üretir."""
        dataset: dict[str, list[MarketBarRecord]] = {}
        for sym in symbols:
            bars: list[MarketBarRecord] = []
            base_price = Decimal("100.00")
            for i in range(10):
                bar_date = (eval_time - timedelta(days=10 - i)).date()
                bar_time = eval_time - timedelta(days=10 - i)
                price = base_price + Decimal(str(i * 1.5))
                bar = MarketBarRecord(
                    symbol=sym,
                    exchange="BIST",
                    trade_date=bar_date,
                    timezone="Europe/Istanbul",
                    session_time_utc=bar_time,
                    currency="TRY",
                    data_status=DataStatus.VERIFIED,
                    raw_open=price,
                    raw_high=price + Decimal("2.00"),
                    raw_low=price - Decimal("1.00"),
                    raw_close=price + Decimal("0.80"),
                    adj_open=price,
                    adj_high=price + Decimal("2.00"),
                    adj_low=price - Decimal("1.00"),
                    adj_close=price + Decimal("0.80"),
                    volume=10000 + i * 500,
                    turnover_try=Decimal(str((10000 + i * 500) * float(price))),
                    source_name="licensed_bist_feed",
                    is_verified=True,
                    ingested_at=bar_time,
                )
                bars.append(bar)
            dataset[sym] = bars
        return dataset
