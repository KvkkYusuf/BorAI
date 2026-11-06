"""Model izleme, veri kayması, performans düşüşü, gecikme ve tahmin dağılımı alarmları.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Veri kayması (data/feature drift): Eğitim dağılımı ile canlı/çıkarım verisi arasındaki
  dağılım farkı Population Stability Index (PSI) ve istatistiki metriklerle izlenir.
- Performans düşüşü (performance degradation): Gerçekleşen piyasa verileri geldiğinde
  yön doğruluğu ve hata metrikleri naif baseline veya kabul eşiklerinin altına
  düştüğünde alarm üretilir.
- Çıkarım gecikmesi (latency): Model tekil veya toplu (batch) çıkarım süreleri SLA
  bütçesini (p95/maks gecikme) aştığında alarm verilir.
- Tahmin dağılımı anomalisi (prediction distribution drift / mode collapse): Modelin hep
  aynı yöne yığılması (%90+ tek yön) veya uç değerler üretmesi tespit edilir.
- Tüm alarmlar ve izleme raporları SPK mevzuatına uygun yasal uyarı içerir.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.indicators.trend import validate_compliant_text

DEFAULT_MONITORING_DISCLAIMER = (
    "Bu model izleme ve alarm metrikleri, teknik sistem kararlılığı, veri kalitesi ve "
    "çıkarım performansının denetlenmesi amacıyla otomatik olarak hesaplanmıştır. "
    "Hiçbir yatırım tavsiyesi, kesinlik veya getiri vaadi içermez. "
    "SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında değildir."
)


class AlertSeverity(StrEnum):
    """Alarm önem derecesi."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MonitoringCategory(StrEnum):
    """İzleme ve alarm kategorisi."""

    DATA_DRIFT = "data_drift"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    LATENCY = "latency"
    PREDICTION_DISTRIBUTION = "prediction_distribution"


class ModelAlert(BaseModel):
    """Üretilen tek bir model izleme alarmı."""

    model_config = ConfigDict(frozen=True)

    alert_id: str = Field(description="Benzersiz alarm kimliği")
    model_id: str = Field(description="İzlenen model kimliği")
    category: MonitoringCategory = Field(description="Alarm kategorisi")
    severity: AlertSeverity = Field(description="Önem derecesi")
    metric_name: str = Field(description="İzlenen metrik adı (örn. 'psi', 'direction_accuracy')")
    current_value: float = Field(description="Ölçülen güncel değer")
    threshold_value: float = Field(description="Aşılan veya ihlal edilen eşik değeri")
    message: str = Field(description="Anlaşılır alarm açıklaması")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Alarmın üretildiği UTC zamanı",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Alarm ile ilişkili ek teknik meta veriler",
    )
    disclaimer: str = Field(
        default=DEFAULT_MONITORING_DISCLAIMER,
        description="SPK uyumlu yasal uyarı metni",
    )

    def model_post_init(self, __context: Any) -> None:
        validate_compliant_text(self.message)
        validate_compliant_text(self.disclaimer)


def calculate_psi(
    baseline: Sequence[float],
    current: Sequence[float],
    num_bins: int = 10,
    epsilon: float = 1e-4,
) -> float:
    """İki sayısal dağılım arasındaki Population Stability Index (PSI) değerini hesaplar.

    Formül: sum((Actual% - Expected%) * ln(Actual% / Expected%))
    Yorumlama:
    - PSI < 0.1: Önemsiz kayma (kararlı)
    - 0.1 <= PSI < 0.25: Orta seviye kayma (izleme / inceleme gerekli)
    - PSI >= 0.25: Ciddi veri kayması (yeniden eğitim veya alarm gerekli)
    """
    if len(baseline) < 2 or len(current) < 2:
        raise ValueError("PSI hesaplamak için her iki dağılımda en az 2 örnek bulunmalıdır.")
    if num_bins < 2:
        raise ValueError("Kova sayısı (num_bins) en az 2 olmalıdır.")

    clean_baseline = [float(x) for x in baseline if not (math.isnan(x) or math.isinf(x))]
    clean_current = [float(x) for x in current if not (math.isnan(x) or math.isinf(x))]

    if len(clean_baseline) < 2 or len(clean_current) < 2:
        raise ValueError("Geçerli sonlu sayı içeren örnek sayısı yetersiz.")

    # Baseline dağılımının yüzdelik dilimlerine göre kova sınırlarını belirle
    sorted_base = sorted(clean_baseline)
    n_base = len(sorted_base)
    bin_edges: list[float] = []

    for i in range(1, num_bins):
        idx = int(round((i / num_bins) * (n_base - 1)))
        bin_edges.append(sorted_base[idx])

    # Yinelenen sınırları filtrele
    unique_edges = sorted(set(bin_edges))
    actual_bins = len(unique_edges) + 1

    # Her iki seriyi bu kovalara dağıt
    def _count_bins(data: list[float], edges: list[float]) -> list[int]:
        counts = [0] * (len(edges) + 1)
        for val in data:
            placed = False
            for b_idx, edge in enumerate(edges):
                if val <= edge:
                    counts[b_idx] += 1
                    placed = True
                    break
            if not placed:
                counts[-1] += 1
        return counts

    base_counts = _count_bins(clean_baseline, unique_edges)
    curr_counts = _count_bins(clean_current, unique_edges)

    total_base = len(clean_baseline)
    total_curr = len(clean_current)

    psi_total = 0.0
    for b_c, c_c in zip(base_counts, curr_counts, strict=False):
        # Sıfır bölme hatasını önlemek için epsilon ekleme
        expected_pct = (b_c + epsilon) / (total_base + actual_bins * epsilon)
        actual_pct = (c_c + epsilon) / (total_curr + actual_bins * epsilon)

        psi_total += (actual_pct - expected_pct) * math.log(actual_pct / expected_pct)

    return max(0.0, float(psi_total))


class DataDriftDetector:
    """Özellik ve veri kayması denetçisi."""

    def __init__(
        self,
        psi_warning_threshold: float = 0.10,
        psi_critical_threshold: float = 0.25,
    ) -> None:
        if psi_warning_threshold <= 0 or psi_critical_threshold <= psi_warning_threshold:
            raise ValueError("Eşik değerleri pozitif ve critical > warning olmalıdır.")
        self.warning_threshold = psi_warning_threshold
        self.critical_threshold = psi_critical_threshold

    def check_feature_drift(
        self,
        model_id: str,
        baseline_features: dict[str, Sequence[float]],
        current_features: dict[str, Sequence[float]],
    ) -> list[ModelAlert]:
        """Tüm ortak özellikler için PSI hesaplar ve eşiği aşanlar için alarm üretir."""
        alerts: list[ModelAlert] = []

        for feature_name, curr_vals in current_features.items():
            if feature_name not in baseline_features:
                continue

            base_vals = baseline_features[feature_name]
            if len(base_vals) < 5 or len(curr_vals) < 5:
                continue

            psi_val = calculate_psi(base_vals, curr_vals)

            if psi_val >= self.critical_threshold:
                alerts.append(
                    ModelAlert(
                        alert_id=f"drift_{model_id}_{feature_name}_crit",
                        model_id=model_id,
                        category=MonitoringCategory.DATA_DRIFT,
                        severity=AlertSeverity.CRITICAL,
                        metric_name=f"psi_{feature_name}",
                        current_value=round(psi_val, 4),
                        threshold_value=self.critical_threshold,
                        message=(
                            f"Model '{model_id}' için '{feature_name}' özelliğinde kritik seviyede "
                            f"veri kayması saptandı "
                            f"(PSI={psi_val:.4f} >= {self.critical_threshold})."
                        ),
                        metadata={"feature": feature_name, "psi": psi_val},
                    )
                )
            elif psi_val >= self.warning_threshold:
                alerts.append(
                    ModelAlert(
                        alert_id=f"drift_{model_id}_{feature_name}_warn",
                        model_id=model_id,
                        category=MonitoringCategory.DATA_DRIFT,
                        severity=AlertSeverity.WARNING,
                        metric_name=f"psi_{feature_name}",
                        current_value=round(psi_val, 4),
                        threshold_value=self.warning_threshold,
                        message=(
                            f"Model '{model_id}' için '{feature_name}' özelliğinde orta seviye "
                            f"veri kayması saptandı "
                            f"(PSI={psi_val:.4f} >= {self.warning_threshold})."
                        ),
                        metadata={"feature": feature_name, "psi": psi_val},
                    )
                )

        return alerts


class PerformanceDegradationDetector:
    """Canlı gerçekleşen verilere göre model performans düşüşü denetçisi."""

    def __init__(
        self,
        min_direction_accuracy_warning: float = 0.52,
        min_direction_accuracy_critical: float = 0.48,
        max_error_multiplier_warning: float = 1.4,
        max_error_multiplier_critical: float = 2.0,
    ) -> None:
        self.acc_warning = min_direction_accuracy_warning
        self.acc_critical = min_direction_accuracy_critical
        self.err_mult_warning = max_error_multiplier_warning
        self.err_mult_critical = max_error_multiplier_critical

    def check_performance(
        self,
        model_id: str,
        predictions: Sequence[float],
        actuals: Sequence[float],
        baseline_mae: float | None = None,
    ) -> list[ModelAlert]:
        """Tahmin ve gerçekleşen verilerden yön doğruluğu ve hata oranını değerlendirir."""
        if len(predictions) != len(actuals):
            raise ValueError("Tahmin ve gerçekleşen değer sayıları eşit olmalıdır.")
        if len(predictions) < 5:
            return []

        alerts: list[ModelAlert] = []

        # 1. Yön doğruluğu (Directional Accuracy)
        correct_direction = 0
        valid_pairs = 0
        abs_errors: list[float] = []

        for p, a in zip(predictions, actuals, strict=True):
            if math.isnan(p) or math.isnan(a):
                continue
            valid_pairs += 1
            abs_errors.append(abs(p - a))

            # Yön işareti eşleşmesi (> 0 veya < 0)
            if (p >= 0 and a >= 0) or (p < 0 and a < 0):
                correct_direction += 1

        if valid_pairs < 5:
            return []

        direction_acc = correct_direction / valid_pairs
        curr_mae = sum(abs_errors) / valid_pairs

        # Yön doğruluğu alarmları
        if direction_acc < self.acc_critical:
            alerts.append(
                ModelAlert(
                    alert_id=f"perf_acc_{model_id}_crit",
                    model_id=model_id,
                    category=MonitoringCategory.PERFORMANCE_DEGRADATION,
                    severity=AlertSeverity.CRITICAL,
                    metric_name="directional_accuracy",
                    current_value=round(direction_acc, 4),
                    threshold_value=self.acc_critical,
                    message=(
                        f"Model '{model_id}' yön doğruluğu kritik eşiğin altına indi "
                        f"(doğruluk={direction_acc:.2%}, kritik eşik={self.acc_critical:.2%})."
                    ),
                    metadata={"samples": valid_pairs, "accuracy": direction_acc},
                )
            )
        elif direction_acc < self.acc_warning:
            alerts.append(
                ModelAlert(
                    alert_id=f"perf_acc_{model_id}_warn",
                    model_id=model_id,
                    category=MonitoringCategory.PERFORMANCE_DEGRADATION,
                    severity=AlertSeverity.WARNING,
                    metric_name="directional_accuracy",
                    current_value=round(direction_acc, 4),
                    threshold_value=self.acc_warning,
                    message=(
                        f"Model '{model_id}' yön doğruluğu uyarı eşiğinin altına indi "
                        f"(doğruluk={direction_acc:.2%}, uyarı eşiği={self.acc_warning:.2%})."
                    ),
                    metadata={"samples": valid_pairs, "accuracy": direction_acc},
                )
            )

        # MAE bozulma alarmları (eğer baseline_mae verilmişse)
        if baseline_mae is not None and baseline_mae > 0:
            error_multiplier = curr_mae / baseline_mae
            if error_multiplier >= self.err_mult_critical:
                alerts.append(
                    ModelAlert(
                        alert_id=f"perf_mae_{model_id}_crit",
                        model_id=model_id,
                        category=MonitoringCategory.PERFORMANCE_DEGRADATION,
                        severity=AlertSeverity.CRITICAL,
                        metric_name="error_multiplier",
                        current_value=round(error_multiplier, 4),
                        threshold_value=self.err_mult_critical,
                        message=(
                            f"Model '{model_id}' ortalama hatası baseline değerinin "
                            f"{error_multiplier:.2f} katına çıktı "
                            f"(güncel MAE={curr_mae:.4f}, baseline MAE={baseline_mae:.4f})."
                        ),
                        metadata={
                            "current_mae": curr_mae,
                            "baseline_mae": baseline_mae,
                            "multiplier": error_multiplier,
                        },
                    )
                )
            elif error_multiplier >= self.err_mult_warning:
                alerts.append(
                    ModelAlert(
                        alert_id=f"perf_mae_{model_id}_warn",
                        model_id=model_id,
                        category=MonitoringCategory.PERFORMANCE_DEGRADATION,
                        severity=AlertSeverity.WARNING,
                        metric_name="error_multiplier",
                        current_value=round(error_multiplier, 4),
                        threshold_value=self.err_mult_warning,
                        message=(
                            f"Model '{model_id}' ortalama hatası baseline seviyesinden saptı "
                            f"(çarpan={error_multiplier:.2f} >= {self.err_mult_warning:.2f})."
                        ),
                        metadata={
                            "current_mae": curr_mae,
                            "baseline_mae": baseline_mae,
                            "multiplier": error_multiplier,
                        },
                    )
                )

        return alerts


class LatencyMonitor:
    """Model çıkarım gecikmesi (inference latency) izleyicisi ve alarm denetçisi."""

    def __init__(
        self,
        p95_warning_ms: float = 150.0,
        p95_critical_ms: float = 400.0,
        max_single_warning_ms: float = 300.0,
        max_single_critical_ms: float = 800.0,
    ) -> None:
        self.p95_warning_ms = p95_warning_ms
        self.p95_critical_ms = p95_critical_ms
        self.max_single_warning_ms = max_single_warning_ms
        self.max_single_critical_ms = max_single_critical_ms

    def check_latencies(
        self,
        model_id: str,
        latencies_ms: Sequence[float],
    ) -> list[ModelAlert]:
        """Gözlemlenen çıkarım sürelerini p95 ve tepe değerlere göre değerlendirir."""
        if not latencies_ms:
            return []

        sorted_latencies = sorted(latencies_ms)
        n = len(sorted_latencies)
        p95_idx = int(math.ceil(0.95 * n)) - 1
        p95_val = sorted_latencies[min(max(0, p95_idx), n - 1)]
        max_val = sorted_latencies[-1]

        alerts: list[ModelAlert] = []

        # p95 gecikme kontrolü
        if p95_val >= self.p95_critical_ms:
            alerts.append(
                ModelAlert(
                    alert_id=f"latency_{model_id}_p95_crit",
                    model_id=model_id,
                    category=MonitoringCategory.LATENCY,
                    severity=AlertSeverity.CRITICAL,
                    metric_name="p95_latency_ms",
                    current_value=round(p95_val, 2),
                    threshold_value=self.p95_critical_ms,
                    message=(
                        f"Model '{model_id}' p95 çıkarım gecikmesi kritik SLA sınırını aştı "
                        f"(p95={p95_val:.1f}ms >= {self.p95_critical_ms:.1f}ms)."
                    ),
                    metadata={"sample_count": n, "p95_ms": p95_val, "max_ms": max_val},
                )
            )
        elif p95_val >= self.p95_warning_ms:
            alerts.append(
                ModelAlert(
                    alert_id=f"latency_{model_id}_p95_warn",
                    model_id=model_id,
                    category=MonitoringCategory.LATENCY,
                    severity=AlertSeverity.WARNING,
                    metric_name="p95_latency_ms",
                    current_value=round(p95_val, 2),
                    threshold_value=self.p95_warning_ms,
                    message=(
                        f"Model '{model_id}' p95 çıkarım gecikmesi uyarı sınırını aştı "
                        f"(p95={p95_val:.1f}ms >= {self.p95_warning_ms:.1f}ms)."
                    ),
                    metadata={"sample_count": n, "p95_ms": p95_val, "max_ms": max_val},
                )
            )

        # Maksimum tepe gecikme kontrolü
        if max_val >= self.max_single_critical_ms:
            alerts.append(
                ModelAlert(
                    alert_id=f"latency_{model_id}_max_crit",
                    model_id=model_id,
                    category=MonitoringCategory.LATENCY,
                    severity=AlertSeverity.CRITICAL,
                    metric_name="max_latency_ms",
                    current_value=round(max_val, 2),
                    threshold_value=self.max_single_critical_ms,
                    message=(
                        f"Model '{model_id}' için ölçülen tepe gecikme kritik seviyede "
                        f"(max={max_val:.1f}ms >= {self.max_single_critical_ms:.1f}ms)."
                    ),
                    metadata={"sample_count": n, "max_ms": max_val},
                )
            )

        return alerts


class PredictionDistributionDetector:
    """Tahmin dağılımı anomalileri ve çökme (mode collapse) denetçisi."""

    def __init__(
        self,
        collapse_threshold_ratio: float = 0.90,
        max_absolute_return_threshold: float = 0.30,
        min_std_dev: float = 1e-4,
    ) -> None:
        self.collapse_threshold = collapse_threshold_ratio
        self.max_abs_return = max_absolute_return_threshold
        self.min_std_dev = min_std_dev

    def check_distribution(
        self,
        model_id: str,
        predictions: Sequence[float],
    ) -> list[ModelAlert]:
        """Tahminlerin dağılımını tek yön çökmesi, sıfır varyans ve uç değerler için denetler."""
        if len(predictions) < 10:
            return []

        clean_preds = [float(p) for p in predictions if not (math.isnan(p) or math.isinf(p))]
        n = len(clean_preds)
        if n < 10:
            return []

        alerts: list[ModelAlert] = []

        # 1. Tek yön çökmesi (Mode collapse - sürekli yukarı veya sürekli aşağı)
        pos_count = sum(1 for p in clean_preds if p > 0)
        neg_count = sum(1 for p in clean_preds if p < 0)
        max_dir_ratio = max(pos_count, neg_count) / n

        if max_dir_ratio >= self.collapse_threshold:
            dir_label = "pozitif (yukarı)" if pos_count > neg_count else "negatif (aşağı)"
            alerts.append(
                ModelAlert(
                    alert_id=f"dist_collapse_{model_id}",
                    model_id=model_id,
                    category=MonitoringCategory.PREDICTION_DISTRIBUTION,
                    severity=AlertSeverity.CRITICAL,
                    metric_name="direction_collapse_ratio",
                    current_value=round(max_dir_ratio, 4),
                    threshold_value=self.collapse_threshold,
                    message=(
                        f"Model '{model_id}' tahmin dağılımında yön çökmesi saptandı: "
                        f"tahminlerin {max_dir_ratio:.1%}'i {dir_label} yönde "
                        f"(eşik={self.collapse_threshold:.1%})."
                    ),
                    metadata={"pos_count": pos_count, "neg_count": neg_count, "total": n},
                )
            )

        # 2. Sıfır / yetersiz varyans (sabit sayı üretme anomalisi)
        mean_val = sum(clean_preds) / n
        variance = sum((p - mean_val) ** 2 for p in clean_preds) / n
        std_dev = math.sqrt(variance)

        if std_dev < self.min_std_dev:
            alerts.append(
                ModelAlert(
                    alert_id=f"dist_zero_var_{model_id}",
                    model_id=model_id,
                    category=MonitoringCategory.PREDICTION_DISTRIBUTION,
                    severity=AlertSeverity.CRITICAL,
                    metric_name="prediction_std_dev",
                    current_value=round(std_dev, 6),
                    threshold_value=self.min_std_dev,
                    message=(
                        f"Model '{model_id}' tahmin varyansı neredeyse sıfır (std={std_dev:.6f}). "
                        "Model sabit/tekrarlayan tahminler üretiyor olabilir."
                    ),
                    metadata={"std_dev": std_dev, "mean": mean_val},
                )
            )

        # 3. İmkansız / aşırı uç değerler (örn. günlük %30 üzeri getiri tahmini)
        extreme_count = sum(1 for p in clean_preds if abs(p) > self.max_abs_return)
        if extreme_count > 0:
            extreme_ratio = extreme_count / n
            alerts.append(
                ModelAlert(
                    alert_id=f"dist_extreme_{model_id}",
                    model_id=model_id,
                    category=MonitoringCategory.PREDICTION_DISTRIBUTION,
                    severity=AlertSeverity.WARNING,
                    metric_name="extreme_outlier_count",
                    current_value=float(extreme_count),
                    threshold_value=0.0,
                    message=(
                        f"Model '{model_id}' için mantık sınırını aşan {extreme_count} adet "
                        f"aşırı getiri tahmini saptandı (|tahmin| > {self.max_abs_return:.1%})."
                    ),
                    metadata={"extreme_count": extreme_count, "ratio": extreme_ratio},
                )
            )

        return alerts


class ModelMonitoringReport(BaseModel):
    """Model izleme döngüsü kapsamlı özet raporu."""

    model_config = ConfigDict(frozen=True)

    report_id: str
    model_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    has_critical_alerts: bool
    has_warning_alerts: bool
    total_alerts: int
    alerts: list[ModelAlert]
    disclaimer: str = Field(default=DEFAULT_MONITORING_DISCLAIMER)

    def model_post_init(self, __context: Any) -> None:
        validate_compliant_text(self.disclaimer)


class ModelMonitoringService:
    """Tüm denetçileri entegre eden merkezi model izleme ve alarm yöneticisi."""

    def __init__(
        self,
        drift_detector: DataDriftDetector | None = None,
        perf_detector: PerformanceDegradationDetector | None = None,
        latency_monitor: LatencyMonitor | None = None,
        dist_detector: PredictionDistributionDetector | None = None,
        alert_handlers: Sequence[Callable[[ModelAlert], None]] | None = None,
    ) -> None:
        self.drift_detector = drift_detector or DataDriftDetector()
        self.perf_detector = perf_detector or PerformanceDegradationDetector()
        self.latency_monitor = latency_monitor or LatencyMonitor()
        self.dist_detector = dist_detector or PredictionDistributionDetector()
        self.alert_handlers = list(alert_handlers or [])
        self._alert_history: list[ModelAlert] = []

    def register_handler(self, handler: Callable[[ModelAlert], None]) -> None:
        """Yeni bir alarm dinleyicisi/tüketicisi ekler."""
        self.alert_handlers.append(handler)

    def run_full_check(
        self,
        model_id: str,
        baseline_features: dict[str, Sequence[float]] | None = None,
        current_features: dict[str, Sequence[float]] | None = None,
        recent_predictions: Sequence[float] | None = None,
        recent_actuals: Sequence[float] | None = None,
        baseline_mae: float | None = None,
        recent_latencies_ms: Sequence[float] | None = None,
    ) -> ModelMonitoringReport:
        """Tüm izleme kontrollerini çalıştırır, alarmları kaydeder ve rapor üretir."""
        collected_alerts: list[ModelAlert] = []

        # 1. Veri kayması denetimi
        if baseline_features and current_features:
            drift_alerts = self.drift_detector.check_feature_drift(
                model_id=model_id,
                baseline_features=baseline_features,
                current_features=current_features,
            )
            collected_alerts.extend(drift_alerts)

        # 2. Performans düşüşü denetimi
        if recent_predictions and recent_actuals:
            perf_alerts = self.perf_detector.check_performance(
                model_id=model_id,
                predictions=recent_predictions,
                actuals=recent_actuals,
                baseline_mae=baseline_mae,
            )
            collected_alerts.extend(perf_alerts)

        # 3. Gecikme denetimi
        if recent_latencies_ms:
            lat_alerts = self.latency_monitor.check_latencies(
                model_id=model_id,
                latencies_ms=recent_latencies_ms,
            )
            collected_alerts.extend(lat_alerts)

        # 4. Tahmin dağılımı denetimi
        if recent_predictions:
            dist_alerts = self.dist_detector.check_distribution(
                model_id=model_id,
                predictions=recent_predictions,
            )
            collected_alerts.extend(dist_alerts)

        # Tetikleme ve geçmişe kayıt
        for alert in collected_alerts:
            self._alert_history.append(alert)
            for handler in self.alert_handlers:
                try:
                    handler(alert)
                except Exception:
                    # Alarm işleyicisi hatası izleme döngüsünü kesmemelidir
                    pass

        has_crit = any(a.severity == AlertSeverity.CRITICAL for a in collected_alerts)
        has_warn = any(a.severity == AlertSeverity.WARNING for a in collected_alerts)

        return ModelMonitoringReport(
            report_id=f"rep_{model_id}_{int(datetime.now(UTC).timestamp())}",
            model_id=model_id,
            has_critical_alerts=has_crit,
            has_warning_alerts=has_warn,
            total_alerts=len(collected_alerts),
            alerts=collected_alerts,
        )

    def get_alert_history(self) -> list[ModelAlert]:
        """Kayıtlı tüm alarmların kopyasını döndürür."""
        return list(self._alert_history)

    def clear_history(self) -> None:
        """Alarm geçmişini temizler."""
        self._alert_history.clear()
