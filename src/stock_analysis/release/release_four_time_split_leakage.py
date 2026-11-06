"""Sürüm 4 (PyTorch Araştırma Hattı) - Zaman Bazlı Split ve Sızıntı Kabul Kapısı.

FAZ-17 Sürüm Planı Kapsamı (docs/phases/FAZ-17-surum-plani.md):
- Sürüm 4 — PyTorch araştırma hattı:
  - Zaman bazlı split ve sızıntı testleri var.

Bu modül:
1. Kronolojik Sıralama ve Rastgele Karıştırma Engeli (Chronological Order & Shuffle Prohibition):
   Finansal zaman serilerinde rastgele satır bölmesi (random split / shuffled K-Fold)
   kesin olarak yasaklanır; verilerin kronolojik sıralılığı doğrulanır.
2. Kesin Bölüm Sınırları ve Ayrıklık (Partition Boundary Purity):
   Eğitim (Train), Doğrulama (Validation) ve Test bölümleri arasında zaman çakışması
   olmaması (Train < Val < Test) güvenceye alınır.
3. Embargo ve Arındırma (Embargo & Purge Gap Window):
   Etiket ufku ve otokorelasyon sızıntısını önlemek için bölümler arası
   yapılandırılabilir arındırma/embargo boşlukları doğrulanır.
4. Gelecek Bilgisi Sızıntısı Engeli (Look-Ahead Bias & Feature Timestamp Safety):
   İleriye dönük kaydırma (negative/forward shift) ve ortalı pencereler (centered rolling
   windows) engellenir; karar anı t ile girdi özellik zaman damgalarının mutlak uyumu denetlenir.
5. Hedef Değişken Sızıntısı (Target Leakage Detection):
   Tahmin hedefinin doğrudan veya mükemmel korelasyon (|r| >= 0.999) ile girdi özelliklerine
   sızması tespit edilir ve reddedilir.
6. Ön İşleme İzolasyonu ve Bölünme Sürümleme (Preprocessing Isolation & Split Registry):
   Normalizer/scaler fit işlemlerinin sadece Train kümesi üzerinde yapılması güvenceye
   alınır; split sınırları SplitRegistry üzerinden SHA-256 içeriği ile değişmez biçimde kaydedilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from stock_analysis.observability.metrics import REGISTRY
from stock_analysis.pipeline.lookahead_bias import (
    LeakageViolationType,
    LookaheadBiasDetector,
)
from stock_analysis.pipeline.split_registry import (
    DataVersionRecord,
    SplitRegistry,
)
from stock_analysis.pipeline.time_series_splitter import (
    DataLeakageError,
    TimeSeriesSplitter,
)

logger = logging.getLogger(__name__)

# Prometheus Telemetri Metrikleri
RELEASE_FOUR_TIME_SPLIT_STATUS = REGISTRY.gauge(
    "release_four_time_split_status",
    "Surum 4 zaman bazli split ve sizinti kabul kapi durumu (1=PASS, 0=FAIL)",
)
RELEASE_FOUR_TIME_SPLIT_CHECKS_TOTAL = REGISTRY.counter(
    "release_four_time_split_checks_total",
    "Toplam Surum 4 zaman bazli split ve sizinti kontrol adimi",
)
RELEASE_FOUR_TIME_SPLIT_PASSED_TOTAL = REGISTRY.counter(
    "release_four_time_split_passed_total",
    "Basariyla gecen Surum 4 zaman bazli split ve sizinti kontrol adimi",
)
RELEASE_FOUR_TIME_SPLIT_FAILED_TOTAL = REGISTRY.counter(
    "release_four_time_split_failed_total",
    "Basarisiz olan Surum 4 zaman bazli split ve sizinti kontrol adimi",
)


class ReleaseFourTimeSplitGateStatus(StrEnum):
    """Sürüm 4 zaman bazlı split ve sızıntı kapısı genel durumları."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True)
class TimeSplitLeakageCheckItem:
    """Tek bir zaman bazlı split veya sızıntı denetim adımının sonucu."""

    check_id: str
    name: str
    passed: bool
    details: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeSplitLeakageVerificationResult:
    """Sürüm 4 zaman bazlı split ve sızıntı kabul kapısının toplu değerlendirme raporu."""

    status: ReleaseFourTimeSplitGateStatus
    total_checks: int
    passed_checks: int
    failed_checks: int
    items: list[TimeSplitLeakageCheckItem]
    evaluated_at_utc: datetime

    @property
    def is_passed(self) -> bool:
        """Tüm kontroller eksiksiz geçti mi?"""
        return self.status == ReleaseFourTimeSplitGateStatus.PASSED and self.failed_checks == 0


class ReleaseFourTimeSplitLeakageGatekeeper:
    """Sürüm 4 PyTorch araştırma hattı için zaman serisi split ve sızıntı kabul denetçisi."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path.cwd()

    def verify_all_split_leakage_criteria(self) -> TimeSplitLeakageVerificationResult:
        """Tüm zaman bazlı split ve sızıntı önleme kriterlerini denetler."""
        items: list[TimeSplitLeakageCheckItem] = [
            self._check_chronological_order_and_shuffle_prevention(),
            self._check_partition_boundary_purity(),
            self._check_embargo_purge_isolation(),
            self._check_lookahead_bias_and_window_safety(),
            self._check_target_leakage_detection(),
            self._check_preprocessing_isolation_and_registry(),
        ]

        passed_count = sum(1 for item in items if item.passed)
        failed_count = sum(1 for item in items if not item.passed)
        total_count = len(items)

        is_all_passed = failed_count == 0 and total_count > 0
        gate_status = (
            ReleaseFourTimeSplitGateStatus.PASSED
            if is_all_passed
            else ReleaseFourTimeSplitGateStatus.FAILED
        )

        # Prometheus telemetrisini güncelle
        RELEASE_FOUR_TIME_SPLIT_CHECKS_TOTAL.inc(total_count)
        RELEASE_FOUR_TIME_SPLIT_PASSED_TOTAL.inc(passed_count)
        RELEASE_FOUR_TIME_SPLIT_FAILED_TOTAL.inc(failed_count)
        RELEASE_FOUR_TIME_SPLIT_STATUS.set(1.0 if is_all_passed else 0.0)

        return TimeSplitLeakageVerificationResult(
            status=gate_status,
            total_checks=total_count,
            passed_checks=passed_count,
            failed_checks=failed_count,
            items=items,
            evaluated_at_utc=datetime.now(UTC),
        )

    def _check_chronological_order_and_shuffle_prevention(self) -> TimeSplitLeakageCheckItem:
        """1. Kronolojik sıralama güvencesi ve rastgele satır karıştırma engeli."""
        # Sıralı veri kümesi
        ordered_sample = [
            {"date": date(2023, 1, 2), "symbol": "THYAO", "close": 120.0},
            {"date": date(2023, 1, 3), "symbol": "THYAO", "close": 122.5},
            {"date": date(2023, 1, 4), "symbol": "THYAO", "close": 121.0},
            {"date": date(2023, 1, 5), "symbol": "THYAO", "close": 124.0},
        ]
        try:
            TimeSeriesSplitter.verify_chronological_order(ordered_sample)
        except Exception as e:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-01-CHRONOLOGICAL-ORDER",
                name="Kronolojik Sıralama ve Rastgele Karıştırma Engeli",
                passed=False,
                details=f"Sıralı veri seti hatalı şekilde reddedildi: {e}",
            )

        # Karışık (shuffled) veri kümesi sızıntı koruması testi
        shuffled_sample = [
            {"date": date(2023, 1, 4), "symbol": "THYAO", "close": 121.0},
            {"date": date(2023, 1, 2), "symbol": "THYAO", "close": 120.0},
        ]
        detected_shuffle_leakage = False
        try:
            TimeSeriesSplitter.verify_chronological_order(shuffled_sample)
        except DataLeakageError:
            detected_shuffle_leakage = True

        if not detected_shuffle_leakage:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-01-CHRONOLOGICAL-ORDER",
                name="Kronolojik Sıralama ve Rastgele Karıştırma Engeli",
                passed=False,
                details="Karışık zaman serisi satırları DataLeakageError fırlatmadı.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-01-CHRONOLOGICAL-ORDER",
            name="Kronolojik Sıralama ve Rastgele Karıştırma Engeli",
            passed=True,
            details=(
                "Kronolojik zaman sıralılığı güvenceye alındı; rastgele satır karıştırma "
                "(shuffled split) DataLeakageError ile bloke edildi."
            ),
            metadata={"shuffle_protection_active": True},
        )

    def _check_partition_boundary_purity(self) -> TimeSplitLeakageCheckItem:
        """2. Kesin bölüm sınırları ve çakışma olmaması (Train < Val < Test)."""
        train = [{"date": date(2023, 1, 2)}, {"date": date(2023, 1, 10)}]
        val = [{"date": date(2023, 1, 11)}, {"date": date(2023, 1, 20)}]
        test = [{"date": date(2023, 1, 21)}, {"date": date(2023, 1, 31)}]

        # Geçerli ayrık sınırlar testi
        try:
            valid_res = TimeSeriesSplitter.verify_no_leakage(train, val, test, embargo_days=0)
            if not valid_res:
                return TimeSplitLeakageCheckItem(
                    check_id="REL4-SPLIT-02-PARTITION-PURITY",
                    name="Kesin Bölüm Sınırları ve Ayrıklık",
                    passed=False,
                    details="Geçerli ayrık bölümler doğrulanamadı.",
                )
        except Exception as e:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-02-PARTITION-PURITY",
                name="Kesin Bölüm Sınırları ve Ayrıklık",
                passed=False,
                details=f"Geçerli bölünme doğrulaması hata verdi: {e}",
            )

        # Çakışan (overlap) durum testi: train ile val çakışması
        overlapping_val = [{"date": date(2023, 1, 9)}, {"date": date(2023, 1, 20)}]
        detected_overlap = False
        try:
            TimeSeriesSplitter.verify_no_leakage(train, overlapping_val, test, embargo_days=0)
        except DataLeakageError:
            detected_overlap = True

        if not detected_overlap:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-02-PARTITION-PURITY",
                name="Kesin Bölüm Sınırları ve Ayrıklık",
                passed=False,
                details="Zaman çakışması (Train-Val overlap) tespit edilemedi.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-02-PARTITION-PURITY",
            name="Kesin Bölüm Sınırları ve Ayrıklık",
            passed=True,
            details=(
                "Eğitim, doğrulama ve test bölümleri arasında kesin kronolojik sınır "
                "ayrıklığı (Train < Val < Test) sağlandı; sınır ihlalleri engellendi."
            ),
            metadata={"strict_temporal_purity": True},
        )

    def _check_embargo_purge_isolation(self) -> TimeSplitLeakageCheckItem:
        """3. Embargo ve arındırma penceresi (purge gap window) koruması."""
        train = [{"date": date(2023, 1, 1)}, {"date": date(2023, 1, 10)}]
        # 5 günlük embargo gereksinimi: min_val > 2023-01-15 olmalı
        val_safe = [{"date": date(2023, 1, 16)}, {"date": date(2023, 1, 25)}]
        test_safe = [{"date": date(2023, 1, 31)}, {"date": date(2023, 2, 10)}]

        try:
            res_safe = TimeSeriesSplitter.verify_no_leakage(
                train, val_safe, test_safe, embargo_days=5
            )
            if not res_safe:
                return TimeSplitLeakageCheckItem(
                    check_id="REL4-SPLIT-03-EMBARGO-ISOLATION",
                    name="Embargo ve Arındırma Boşluğu (Purge Gap)",
                    passed=False,
                    details="Embargo uyumlu güvenli aralık doğrulanamadı.",
                )
        except Exception as e:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-03-EMBARGO-ISOLATION",
                name="Embargo ve Arındırma Boşluğu (Purge Gap)",
                passed=False,
                details=f"Embargo doğrulaması beklenmedik hata verdi: {e}",
            )

        # Embargo ihlali testi: 12 Ocak (10 Ocak + 5 gün = 15 Ocak kuralına aykırı)
        val_violating = [{"date": date(2023, 1, 12)}, {"date": date(2023, 1, 25)}]
        detected_embargo_violation = False
        try:
            TimeSeriesSplitter.verify_no_leakage(train, val_violating, test_safe, embargo_days=5)
        except DataLeakageError:
            detected_embargo_violation = True

        if not detected_embargo_violation:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-03-EMBARGO-ISOLATION",
                name="Embargo ve Arındırma Boşluğu (Purge Gap)",
                passed=False,
                details="Embargo penceresi ihlali DataLeakageError fırlatmadı.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-03-EMBARGO-ISOLATION",
            name="Embargo ve Arındırma Boşluğu (Purge Gap)",
            passed=True,
            details=(
                "Etiket ufku ve otokorelasyon arındırması için embargo aralıkları "
                "(embargo_days) başarıyla uygulandı ve ihlaller engellendi."
            ),
            metadata={"embargo_verification": "ACTIVE"},
        )

    def _check_lookahead_bias_and_window_safety(self) -> TimeSplitLeakageCheckItem:
        """4. Gelecek bilgisi sızıntısı (lookahead bias) ve pencere güvenliği."""
        # Centered rolling window engeli testi
        centered_rejected = False
        try:
            LookaheadBiasDetector.verify_window_configuration(window_size=10, is_centered=True)
        except DataLeakageError:
            centered_rejected = True

        if not centered_rejected:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY",
                name="Gelecek Bilgisi Sızıntısı ve Pencere Güvenliği",
                passed=False,
                details="Ortalanmış kayan pencere (is_centered=True) reddedilmedi.",
            )

        # Negatif/ileri kaydırma (forward shift) engeli testi
        forward_shift_rejected = False
        try:
            LookaheadBiasDetector.verify_window_configuration(window_size=10, forward_shift=2)
        except DataLeakageError:
            forward_shift_rejected = True

        if not forward_shift_rejected:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY",
                name="Gelecek Bilgisi Sızıntısı ve Pencere Güvenliği",
                passed=False,
                details="İleriye dönük kaydırma (forward_shift > 0) reddedilmedi.",
            )

        # Karar anı t ile özellik zaman damgası uyumu testi
        decision_dt = date(2023, 5, 10)
        feature_timestamps = {
            "sma_20": date(2023, 5, 10),
            "future_leak_feature": date(2023, 5, 11),  # Gelecek zaman damgası
        }
        violations = LookaheadBiasDetector.check_feature_timestamp_alignment(
            decision_dt, feature_timestamps
        )
        has_future_ts_violation = any(
            v.violation_type == LeakageViolationType.FUTURE_TIMESTAMP for v in violations
        )
        if not has_future_ts_violation:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY",
                name="Gelecek Bilgisi Sızıntısı ve Pencere Güvenliği",
                passed=False,
                details="Karar anından sonraki özellik zaman damgası ihlali tespit edilemedi.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-04-LOOKAHEAD-BIAS-SAFETY",
            name="Gelecek Bilgisi Sızıntısı ve Pencere Güvenliği",
            passed=True,
            details=(
                "Ortalanmış pencereler (center=True), ileriye dönük kaydırmalar "
                "(forward shift) ve t sonrasındaki özellik zaman damgaları kesin olarak engellendi."
            ),
            metadata={"lookahead_detector_status": "ACTIVE"},
        )

    def _check_target_leakage_detection(self) -> TimeSplitLeakageCheckItem:
        """5. Tahmin hedefi sızıntısı ve mükemmel korelasyon denetimi."""
        # Hedefin girdi özelliklerine doğrudan verilmesi
        records = [
            {"date": "2023-01-02", "feat1": 10.0, "target_return": 0.02},
            {"date": "2023-01-03", "feat1": 11.0, "target_return": 0.03},
            {"date": "2023-01-04", "feat1": 12.0, "target_return": -0.01},
        ]
        violations_direct = LookaheadBiasDetector.check_target_leakage(
            records,
            target_name="target_return",
            feature_names=["feat1", "target_return"],
        )
        has_direct_target = any(
            v.violation_type == LeakageViolationType.TARGET_LEAKAGE for v in violations_direct
        )
        if not has_direct_target:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-05-TARGET-LEAKAGE",
                name="Hedef Değişken Sızıntısı (Target Leakage)",
                passed=False,
                details="Hedef sütunun doğrudan özellikler arasına sızması tespit edilemedi.",
            )

        # Mükemmel korelasyonlu yapay sızıntı kontrolü
        records_correlated = [
            {"date": "2023-01-02", "leaked_col": 0.02, "target_return": 0.02},
            {"date": "2023-01-03", "leaked_col": 0.03, "target_return": 0.03},
            {"date": "2023-01-04", "leaked_col": -0.01, "target_return": -0.01},
        ]
        violations_corr = LookaheadBiasDetector.check_target_leakage(
            records_correlated,
            target_name="target_return",
            feature_names=["leaked_col"],
            correlation_threshold=0.999,
        )
        has_corr_target = any(
            v.violation_type == LeakageViolationType.TARGET_LEAKAGE for v in violations_corr
        )
        if not has_corr_target:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-05-TARGET-LEAKAGE",
                name="Hedef Değişken Sızıntısı (Target Leakage)",
                passed=False,
                details="Hedefle 1.0 korelasyonlu özellik sızıntısı tespit edilemedi.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-05-TARGET-LEAKAGE",
            name="Hedef Değişken Sızıntısı (Target Leakage)",
            passed=True,
            details=(
                "Hedef değişkenin doğrudan girdi sütunlarına sızması ve mükemmel korelasyonlu "
                "(|r| >= 0.999) etiket kopyalanması koruması doğrulandı."
            ),
            metadata={"target_leakage_detector_status": "ACTIVE"},
        )

    def _check_preprocessing_isolation_and_registry(self) -> TimeSplitLeakageCheckItem:
        """6. Ön işleme parametre izolasyonu ve bölünme kayıt defteri (Split Registry)."""
        train_data = [{"feat": 10.0}, {"feat": 20.0}]  # mean = 15.0
        eval_data = [{"feat": 100.0}, {"feat": 110.0}]  # pooled mean = 60.0

        # Eğer scaler tüm veriye fit edildiyse sızıntı yakalanmalı
        leaked_scaler_params = {"feat": {"mean": 60.0, "std": 1.0}}
        scaler_violations = LookaheadBiasDetector.verify_preprocessing_isolation(
            train_data, eval_data, fitted_scaler_params=leaked_scaler_params
        )
        has_scaler_leakage = any(
            v.violation_type == LeakageViolationType.GLOBAL_PREPROCESSING_LEAKAGE
            for v in scaler_violations
        )
        if not has_scaler_leakage:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY",
                name="Ön İşleme İzolasyonu ve Bölünme Kayıt Defteri",
                passed=False,
                details="Tüm veri üzerinde fit edilmiş scaler sızıntısı yakalanamadı.",
            )

        # SplitRegistry değişmezlik ve sürümleme doğrulaması
        registry = SplitRegistry()
        sample_records = [
            {"date": date(2023, 1, 2), "symbol": "THYAO", "close": 100.0},
            {"date": date(2023, 1, 3), "symbol": "THYAO", "close": 105.0},
            {"date": date(2023, 1, 4), "symbol": "THYAO", "close": 102.0},
            {"date": date(2023, 1, 5), "symbol": "THYAO", "close": 108.0},
        ]
        content_hash = SplitRegistry.compute_content_hash(sample_records)
        version_rec = registry.register_version(
            DataVersionRecord(
                dataset_id="bist-research-data",
                version_tag="v1.0.0",
                content_sha256=content_hash,
                source_uri="kaggle://bist-daily-stock-historical-data",
                record_count=len(sample_records),
            )
        )
        if not version_rec.content_sha256:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY",
                name="Ön İşleme İzolasyonu ve Bölünme Kayıt Defteri",
                passed=False,
                details="Veri sürümü için SHA-256 sağlama toplamı üretilemedi.",
            )

        # Split kaydı oluşturma
        split_result, split_rec = registry.create_and_register_ratio_split(
            dataset_id="bist-research-data",
            version_tag="v1.0.0",
            records=sample_records,
            split_id="pilot-temporal-split",
            train_ratio=0.50,
            val_ratio=0.25,
            test_ratio=0.25,
        )
        if split_rec.dataset_id != "bist-research-data" or not split_result.is_leak_free:
            return TimeSplitLeakageCheckItem(
                check_id="REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY",
                name="Ön İşleme İzolasyonu ve Bölünme Kayıt Defteri",
                passed=False,
                details="Split kaydı registry içinde geçerli kaydedilemedi.",
            )

        return TimeSplitLeakageCheckItem(
            check_id="REL4-SPLIT-06-PREPROCESSING-AND-REGISTRY",
            name="Ön İşleme İzolasyonu ve Bölünme Kayıt Defteri",
            passed=True,
            details=(
                "Ön işleme parametrelerinin (scaler/normalizer) yalnızca Train üzerinde "
                "fit edilmesi güvenceye alındı; SplitRegistry ile SHA-256 sürümleme ve "
                "bölünme değişmezliği doğrulandı."
            ),
            metadata={
                "scaler_isolation": True,
                "split_registry_verified": True,
                "dataset_sha256": version_rec.content_sha256[:16],
            },
        )


def generate_markdown_report(result: TimeSplitLeakageVerificationResult) -> str:
    """Sürüm 4 zaman bazlı split ve sızıntı kabul doğrulama raporunu Markdown olarak üretir."""
    status_badge = "✅ GEÇTİ (PASS)" if result.is_passed else "❌ BAŞARISIZ (FAIL)"
    timestamp_str = result.evaluated_at_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Sürüm 4 — Zaman Bazlı Split ve Sızıntı Kabul Raporu",
        "",
        "> **İlgili Faz:** FAZ-17 Sürüm Planı (Sürüm 4 — PyTorch araştırma hattı)  ",
        f"> **Değerlendirme Zamanı:** {timestamp_str}  ",
        f"> **Genel Kapı Durumu:** **{status_badge}**  ",
        f"> **Başarı Oranı:** {result.passed_checks} / {result.total_checks} kontrol  ",
        "",
        "## 1. Kriter Değerlendirme Özeti",
        "",
        "| No | Kontrol Kimliği | Kontrol Adı | Durum | Detaylar |",
        "|---|---|---|---|---|",
    ]

    for idx, item in enumerate(result.items, 1):
        st = "✅ GEÇTİ" if item.passed else "❌ KALDI"
        lines.append(f"| {idx} | `{item.check_id}` | {item.name} | {st} | {item.details} |")

    lines.extend(
        [
            "",
            "## 2. Zaman Serisi Sızıntı Önleme Metodolojisi",
            "",
            "- **Kronolojik Ayrım:** Rastgele satır karıştırma (shuffling) yasaklanmıştır.",
            "- **Bölüm Ayrıklığı:** `Train < Validation < Test` kronolojik sınırları katıdır.",
            (
                "- **Embargo Penceresi:** Etiket ufku ve otokorelasyon arındırması için "
                "`embargo_days` uygulanır."
            ),
            (
                "- **Gelecek Bilgisi Güvenliği:** `center=True` ve `shift(-k)` gibi geleceğe "
                "bakan pencereler yasaklanmıştır."
            ),
            (
                "- **Hedef Değişken Denetimi:** Doğrudan girdi sütunu sızıntısı ve "
                "$|r| \\ge 0.999$ aşırı korelasyon elenir."
            ),
            (
                "- **Ön İşleme İzolasyonu:** Scaler/normalizer fit parametreleri sadece "
                "`Train` kümesinden elde edilir."
            ),
            (
                "- **Kayıt Defteri & Değişmezlik:** `SplitRegistry` üzerinden SHA-256 ile "
                "sürümleme ve tekrarlanabilirlik denetlenir."
            ),
            "",
            "---",
            (
                "*Rapor, `stock_analysis.release.release_four_time_split_leakage` "
                "tarafından otomatik oluşturulmuştur.*"
            ),
        ]
    )

    return "\n".join(lines)
