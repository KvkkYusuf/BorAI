"""Model performance observability: offline validation and live realized outcome tracking.

In accordance with FAZ-15 & .agent/rules/veri-ml.md:
- Offline validation metrics (holdout / walk-forward directional accuracy, Brier score, MAE, RMSE)
  and live realized performance (outcomes observed after prediction horizon matures)
  are tracked separately.
- Generalization gap and performance degradation are continuously measured to detect
  model decay, distribution drift, and prediction failure.
- Statuses (healthy, degraded, critical, insufficient_data) are exposed via Prometheus
  gauges and FastAPI management endpoints.
- Purely technical model monitoring without investment advice (SPK disclaimer included).
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.observability.metrics import (
    MODEL_LIVE_BRIER_SCORE,
    MODEL_LIVE_DIRECTIONAL_ACCURACY,
    MODEL_LIVE_MAE,
    MODEL_LIVE_PREDICTIONS_TOTAL,
    MODEL_OFFLINE_BRIER_SCORE,
    MODEL_OFFLINE_DIRECTIONAL_ACCURACY,
    MODEL_OFFLINE_MAE,
    MODEL_PERFORMANCE_GAP_ACCURACY,
    MODEL_PERFORMANCE_STATUS,
)

DEFAULT_MODEL_PERFORMANCE_DISCLAIMER = (
    "Bu model performans ve izleme metrikleri, teknik sistem kararlılığı ve "
    "algoritmik modellerin çevrimdışı/canlı doğruluk denetimi amacıyla otomatik üretilmiştir. "
    "Hiçbir yatırım tavsiyesi, kesinlik veya getiri vaadi içermez. "
    "SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında değildir."
)


class ModelHealthStatus(StrEnum):
    """Model operasyonel sağlık ve performans durumu."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    INSUFFICIENT_DATA = "insufficient_data"

    @property
    def numeric_code(self) -> float:
        """Numeric status code for Prometheus gauge exposition."""
        codes = {
            ModelHealthStatus.HEALTHY: 0.0,
            ModelHealthStatus.DEGRADED: 1.0,
            ModelHealthStatus.CRITICAL: 2.0,
            ModelHealthStatus.INSUFFICIENT_DATA: 3.0,
        }
        return codes[self]


class OfflineValidationMetrics(BaseModel):
    """Eğitim/doğrulama aşamasında hesaplanan çevrimdışı başarım ölçütleri."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(description="İzlenen model kimliği (örn. 'bist_lstm_v1')")
    version: str = Field(description="Model sürümü (örn. '1.0.0')")
    evaluation_date: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Çevrimdışı değerlendirme zamanı",
    )
    dataset_split: str = Field(
        default="walk_forward",
        description="Veri kümesi ayrımı (örn. 'walk_forward', 'test_holdout')",
    )
    sample_count: int = Field(ge=1, description="Değerlendirilen test örnek sayısı")
    directional_accuracy: float = Field(
        ge=0.0, le=1.0, description="Yön doğruluğu oranı (0.0 - 1.0)"
    )
    brier_score: float = Field(
        ge=0.0, le=2.0, description="Olasılık kalibrasyon Brier skoru (0 mükemmel, 1 kötü)"
    )
    mae: float = Field(ge=0.0, description="Ortalama mutlak hata (Mean Absolute Error)")
    rmse: float = Field(ge=0.0, description="Kök ortalama kare hata (RMSE)")
    baseline_accuracy: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Kıyaslanan naif baseline doğruluk oranı",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Ek değerlendirme parametreleri"
    )


class LivePredictionRecord(BaseModel):
    """Üretilen canlı tahmin kaydı ve olgunlaşan gerçekleşme sonucu."""

    model_config = ConfigDict(frozen=True)

    prediction_id: str = Field(description="Benzersiz tahmin takip kimliği")
    model_id: str = Field(description="Tahmini üreten model kimliği")
    version: str = Field(description="Tahmini üreten model sürümü")
    symbol: str = Field(description="Tahmin yapılan hisse/sembol (örn. 'THYAO.IS')")
    prediction_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Tahminin yapıldığı UTC anı",
    )
    horizon_hours: int = Field(ge=1, description="Tahmin vadesi (saat)")
    predicted_value: float = Field(description="Öngörülen getiri veya fiyat seviyesi")
    predicted_direction: int = Field(
        description="Tahmin yönü (+1 yükseliş, -1 düşüş, 0 yatay)",
    )
    predicted_probability: float = Field(ge=0.0, le=1.0, description="Tahmin edilen yön olasılığı")
    # Realized market outcome fields
    actual_value: float | None = Field(
        default=None, description="Vade sonunda gerçekleşen gerçek getiri veya fiyat"
    )
    actual_direction: int | None = Field(
        default=None, description="Gerçekleşen yön (+1 yükseliş, -1 düşüş, 0 yatay)"
    )
    realized_timestamp: datetime | None = Field(
        default=None, description="Gerçekleşen verinin sisteme girdiği UTC anı"
    )
    is_realized: bool = Field(
        default=False, description="Tahminin vadesi dolup piyasa verisiyle eşleşti mi?"
    )
    is_direction_correct: bool | None = Field(default=None, description="Yön tahmini doğru mu?")
    absolute_error: float | None = Field(
        default=None, description="Mutlak hata (|predicted - actual|)"
    )
    brier_loss: float | None = Field(
        default=None, description="Tekil Brier kaybı (probability - binary_outcome)^2"
    )


class LivePerformanceMetrics(BaseModel):
    """Canlı gerçekleşen verilere göre hesaplanan anlık performans metrikleri."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    version: str
    total_predictions: int = Field(ge=0, description="Toplam üretilen canlı tahmin sayısı")
    realized_predictions: int = Field(
        ge=0, description="Gerçekleşmesi tamamlanmış ve değerlendirilen tahmin sayısı"
    )
    pending_predictions: int = Field(
        ge=0, description="Henüz vadesi dolmamış bekleyen tahmin sayısı"
    )
    directional_accuracy: float | None = Field(default=None, description="Canlı yön doğruluk oranı")
    brier_score: float | None = Field(default=None, description="Canlı gerçekleşen Brier skoru")
    mae: float | None = Field(default=None, description="Canlı ortalama mutlak hata")
    rmse: float | None = Field(default=None, description="Canlı RMSE")
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Metriklerin son hesaplanma zamanı",
    )


class ModelPerformanceComparison(BaseModel):
    """Çevrimdışı ve canlı performansın ayrı ve karşılaştırmalı analizi."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    version: str
    status: ModelHealthStatus
    offline_metrics: OfflineValidationMetrics | None = None
    live_metrics: LivePerformanceMetrics
    accuracy_gap: float | None = Field(
        default=None,
        description="Çevrimdışı - Canlı doğruluk farkı (pozitifse canlıda performans düşüşü var)",
    )
    brier_gap: float | None = Field(
        default=None,
        description="Canlı Brier - Çevrimdışı Brier farkı (pozitifse kalibrasyon bozulmuş)",
    )
    mae_degradation_ratio: float | None = Field(
        default=None,
        description="Canlı MAE / Çevrimdışı MAE oranı (> 1.0 ise hata büyümüş)",
    )
    status_reason: str = Field(description="Mevcut durumun gerekçesi")
    last_evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    disclaimer: str = Field(default=DEFAULT_MODEL_PERFORMANCE_DISCLAIMER)

    def model_post_init(self, __context: Any) -> None:
        validate_compliant_text(self.status_reason)
        validate_compliant_text(self.disclaimer)


class PerformanceTrackingConfig(BaseModel):
    """Model performans takip ve alarm eşikleri yapılandırması."""

    model_config = ConfigDict(frozen=True)

    min_live_samples_for_evaluation: int = Field(
        default=10, description="Anlamlı canlı değerlendirme için gereken asgari örnek sayısı"
    )
    accuracy_degradation_warning_gap: float = Field(
        default=0.05,
        description="Canlı doğrulukta uyarı üretecek kabul edilebilir azami düşüş (%5)",
    )
    accuracy_degradation_critical_gap: float = Field(
        default=0.10,
        description="Canlı doğrulukta kritik alarm üretecek azami düşüş (%10)",
    )
    min_live_accuracy_critical: float = Field(
        default=0.50,
        description="Rastgele tahminden (yazı-tura) kötü sayılacak taban canlı doğruluk",
    )
    max_mae_multiplier_critical: float = Field(
        default=2.0,
        description="Çevrimdışı MAE'nin bu katı aşıldığında kritik sayılır",
    )


class ModelPerformanceTracker:
    """Çevrimdışı doğrulama ve canlı gerçekleşen performans ayrımını izleyen yönetici."""

    def __init__(self, config: PerformanceTrackingConfig | None = None) -> None:
        self.config = config or PerformanceTrackingConfig()
        self._lock = threading.Lock()
        # Key: (model_id, version) -> OfflineValidationMetrics
        self._offline_metrics: dict[tuple[str, str], OfflineValidationMetrics] = {}
        # Key: prediction_id -> LivePredictionRecord
        self._predictions: dict[str, LivePredictionRecord] = {}

    def register_offline_validation(self, metrics: OfflineValidationMetrics) -> None:
        """Çevrimdışı doğrulama veya walk-forward test sonuçlarını kaydeder."""
        key = (metrics.model_id, metrics.version)
        labels = {"model_id": metrics.model_id, "version": metrics.version}

        with self._lock:
            self._offline_metrics[key] = metrics

        # Update Prometheus gauges
        MODEL_OFFLINE_DIRECTIONAL_ACCURACY.set(metrics.directional_accuracy, labels=labels)
        MODEL_OFFLINE_BRIER_SCORE.set(metrics.brier_score, labels=labels)
        MODEL_OFFLINE_MAE.set(metrics.mae, labels=labels)

    def get_offline_validation(
        self, model_id: str, version: str | None = None
    ) -> OfflineValidationMetrics | None:
        """Kayıtlı çevrimdışı metrikleri getirir."""
        with self._lock:
            if version is not None:
                return self._offline_metrics.get((model_id, version))
            # Sürüm verilmemişse en güncel sürümü bul
            candidates = [m for (m_id, _), m in self._offline_metrics.items() if m_id == model_id]
            if not candidates:
                return None
            return max(candidates, key=lambda m: m.evaluation_date)

    def record_live_prediction(self, prediction: LivePredictionRecord) -> None:
        """Canlı çıkarım anında üretilen tahmini bekleyen kayıt olarak ekler."""
        with self._lock:
            self._predictions[prediction.prediction_id] = prediction

    def record_realized_outcome(
        self,
        prediction_id: str,
        actual_value: float,
        actual_direction: int | None = None,
        realized_timestamp: datetime | None = None,
    ) -> LivePredictionRecord | None:
        """Vade sonunda gerçekleşen piyasa sonucunu tahmin ile eşleştirir."""
        realized_time = realized_timestamp or datetime.now(UTC)

        with self._lock:
            record = self._predictions.get(prediction_id)
            if not record:
                return None

            # Calculate direction if not explicitly supplied
            if actual_direction is None:
                if actual_value > 0.00001:
                    computed_direction = 1
                elif actual_value < -0.00001:
                    computed_direction = -1
                else:
                    computed_direction = 0
            else:
                computed_direction = actual_direction

            # Direction match:
            # If predicted direction matches actual direction
            is_correct = (record.predicted_direction == computed_direction) or (
                record.predicted_direction > 0
                and computed_direction > 0
                or record.predicted_direction < 0
                and computed_direction < 0
            )

            abs_error = abs(record.predicted_value - actual_value)

            # Brier loss for binary directional outcome (up=1, not up=0)
            target_binary = 1.0 if computed_direction > 0 else 0.0
            brier_loss = (record.predicted_probability - target_binary) ** 2

            updated_record = LivePredictionRecord(
                prediction_id=record.prediction_id,
                model_id=record.model_id,
                version=record.version,
                symbol=record.symbol,
                prediction_timestamp=record.prediction_timestamp,
                horizon_hours=record.horizon_hours,
                predicted_value=record.predicted_value,
                predicted_direction=record.predicted_direction,
                predicted_probability=record.predicted_probability,
                actual_value=actual_value,
                actual_direction=computed_direction,
                realized_timestamp=realized_time,
                is_realized=True,
                is_direction_correct=is_correct,
                absolute_error=abs_error,
                brier_loss=brier_loss,
            )
            self._predictions[prediction_id] = updated_record

        # Increment outcome counter in Prometheus
        outcome_label = "correct" if is_correct else "incorrect"
        MODEL_LIVE_PREDICTIONS_TOTAL.inc(
            labels={"model_id": updated_record.model_id, "outcome": outcome_label}
        )

        # Trigger model metric re-evaluation and gauge updates
        self.evaluate_model(updated_record.model_id, updated_record.version)

        return updated_record

    def get_live_metrics(self, model_id: str, version: str | None = None) -> LivePerformanceMetrics:
        """Model için gerçekleşmiş canlı tahminlerden metrikleri hesaplar."""
        with self._lock:
            matched_records = [
                r
                for r in self._predictions.values()
                if r.model_id == model_id and (version is None or r.version == version)
            ]

        total = len(matched_records)
        realized = [r for r in matched_records if r.is_realized]
        pending = total - len(realized)

        resolved_version = version or (realized[0].version if realized else "unknown")

        if not realized:
            return LivePerformanceMetrics(
                model_id=model_id,
                version=resolved_version,
                total_predictions=total,
                realized_predictions=0,
                pending_predictions=pending,
                directional_accuracy=None,
                brier_score=None,
                mae=None,
                rmse=None,
            )

        # Directional Accuracy
        correct_count = sum(1 for r in realized if r.is_direction_correct)
        accuracy = correct_count / len(realized)

        # Brier Score
        brier_losses = [r.brier_loss for r in realized if r.brier_loss is not None]
        brier_score = sum(brier_losses) / len(brier_losses) if brier_losses else None

        # MAE and RMSE
        abs_errors = [r.absolute_error for r in realized if r.absolute_error is not None]
        mae = sum(abs_errors) / len(abs_errors) if abs_errors else None
        rmse = math.sqrt(sum(e**2 for e in abs_errors) / len(abs_errors)) if abs_errors else None

        return LivePerformanceMetrics(
            model_id=model_id,
            version=resolved_version,
            total_predictions=total,
            realized_predictions=len(realized),
            pending_predictions=pending,
            directional_accuracy=round(accuracy, 4),
            brier_score=round(brier_score, 4) if brier_score is not None else None,
            mae=round(mae, 4) if mae is not None else None,
            rmse=round(rmse, 4) if rmse is not None else None,
        )

    def evaluate_model(
        self, model_id: str, version: str | None = None
    ) -> ModelPerformanceComparison:
        """Çevrimdışı ve canlı performansı karşılaştırıp sağlık durumunu ve sapmayı belirler."""
        offline = self.get_offline_validation(model_id, version)
        resolved_version = version or (offline.version if offline else "1.0.0")
        live = self.get_live_metrics(model_id, resolved_version)

        labels = {"model_id": model_id, "version": resolved_version}

        # Insufficient data check
        if live.realized_predictions < self.config.min_live_samples_for_evaluation:
            status = ModelHealthStatus.INSUFFICIENT_DATA
            status_reason = (
                "Yeterli canlı gerçekleşme verisi henüz toplanmadı "
                f"({live.realized_predictions}/{self.config.min_live_samples_for_evaluation} "
                "örnek)."
            )
            comparison = ModelPerformanceComparison(
                model_id=model_id,
                version=resolved_version,
                status=status,
                offline_metrics=offline,
                live_metrics=live,
                accuracy_gap=None,
                brier_gap=None,
                mae_degradation_ratio=None,
                status_reason=status_reason,
            )
            MODEL_PERFORMANCE_STATUS.set(status.numeric_code, labels=labels)
            return comparison

        assert live.directional_accuracy is not None
        assert live.mae is not None

        # Update Live Prometheus Gauges
        MODEL_LIVE_DIRECTIONAL_ACCURACY.set(live.directional_accuracy, labels=labels)
        if live.brier_score is not None:
            MODEL_LIVE_BRIER_SCORE.set(live.brier_score, labels=labels)
        MODEL_LIVE_MAE.set(live.mae, labels=labels)

        # Comparative analysis
        accuracy_gap: float | None = None
        brier_gap: float | None = None
        mae_ratio: float | None = None
        status = ModelHealthStatus.HEALTHY
        status_reasons: list[str] = []

        if offline is not None:
            accuracy_gap = round(offline.directional_accuracy - live.directional_accuracy, 4)
            MODEL_PERFORMANCE_GAP_ACCURACY.set(accuracy_gap, labels=labels)

            if live.brier_score is not None:
                brier_gap = round(live.brier_score - offline.brier_score, 4)

            safe_offline_mae = max(offline.mae, 1e-6)
            mae_ratio = round(live.mae / safe_offline_mae, 4)

            # Check degradation conditions
            if (
                live.directional_accuracy < self.config.min_live_accuracy_critical
                or accuracy_gap >= self.config.accuracy_degradation_critical_gap
                or mae_ratio >= self.config.max_mae_multiplier_critical
            ):
                status = ModelHealthStatus.CRITICAL
                status_reasons.append(
                    f"Kritik model performans gerilemesi saptandı: "
                    f"Canlı doğruluk={live.directional_accuracy:.2%}, "
                    f"Sapma farkı={accuracy_gap:.2%}, Hata artış oranı={mae_ratio:.2f}x."
                )
            elif accuracy_gap >= self.config.accuracy_degradation_warning_gap:
                threshold_pct = f"{self.config.accuracy_degradation_warning_gap:.2%}"
                status = ModelHealthStatus.DEGRADED
                status_reasons.append(
                    "Canlı doğrulukta orta seviye performans düşüşü gözlendi: "
                    f"Sapma={accuracy_gap:.2%} (eşik={threshold_pct})."
                )
            else:
                status_reasons.append(
                    f"Model çevrimdışı doğrulama toleransı dahilinde kararlı çalışıyor "
                    f"(Doğruluk={live.directional_accuracy:.2%})."
                )
        else:
            # No offline benchmark registered
            if live.directional_accuracy < self.config.min_live_accuracy_critical:
                crit_thresh_pct = f"{self.config.min_live_accuracy_critical:.2%}"
                status = ModelHealthStatus.CRITICAL
                status_reasons.append(
                    "Çevrimdışı referans yok; canlı doğruluk kritik eşiğin altında: "
                    f"{live.directional_accuracy:.2%} < {crit_thresh_pct}."
                )
            else:
                status_reasons.append(
                    "Çevrimdışı referans kaydı bulunamadı; "
                    f"canlı yön doğruluğu={live.directional_accuracy:.2%}."
                )

        final_reason = " ".join(status_reasons)
        MODEL_PERFORMANCE_STATUS.set(status.numeric_code, labels=labels)

        return ModelPerformanceComparison(
            model_id=model_id,
            version=resolved_version,
            status=status,
            offline_metrics=offline,
            live_metrics=live,
            accuracy_gap=accuracy_gap,
            brier_gap=brier_gap,
            mae_degradation_ratio=mae_ratio,
            status_reason=final_reason,
        )

    def get_summary_report(self) -> dict[str, Any]:
        """Tüm kayıtlı modellerin çevrimdışı vs canlı performans özetini sunar."""
        with self._lock:
            all_model_keys = set(self._offline_metrics.keys())
            for record in self._predictions.values():
                all_model_keys.add((record.model_id, record.version))

        evaluations: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {
            ModelHealthStatus.HEALTHY.value: 0,
            ModelHealthStatus.DEGRADED.value: 0,
            ModelHealthStatus.CRITICAL.value: 0,
            ModelHealthStatus.INSUFFICIENT_DATA.value: 0,
        }

        for model_id, version in sorted(all_model_keys):
            comp = self.evaluate_model(model_id, version)
            status_counts[comp.status.value] += 1
            evaluations.append(comp.model_dump(mode="json"))

        return {
            "monitored_models_count": len(all_model_keys),
            "status_summary": status_counts,
            "models": evaluations,
            "disclaimer": DEFAULT_MODEL_PERFORMANCE_DISCLAIMER,
        }

    def get_pending_predictions(self, model_id: str | None = None) -> list[LivePredictionRecord]:
        """Vadesi henüz dolmamış bekleyen canlı tahminleri listeler."""
        with self._lock:
            return [
                r
                for r in self._predictions.values()
                if not r.is_realized and (model_id is None or r.model_id == model_id)
            ]

    def get_realized_predictions(
        self, model_id: str | None = None, limit: int = 100
    ) -> list[LivePredictionRecord]:
        """Gerçekleşmiş canlı tahmin kayıtlarını getirir."""
        with self._lock:
            realized = [
                r
                for r in self._predictions.values()
                if r.is_realized and (model_id is None or r.model_id == model_id)
            ]
            realized.sort(
                key=lambda r: r.realized_timestamp or r.prediction_timestamp,
                reverse=True,
            )
            return realized[:limit]

    def reset(self) -> None:
        """Test izolasyonu için izleyici hafızasını temizler."""
        with self._lock:
            self._offline_metrics.clear()
            self._predictions.clear()


# Global singleton instance
MODEL_PERFORMANCE_TRACKER = ModelPerformanceTracker()
