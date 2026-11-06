"""Model gölge modu (Shadow Mode) takip ve canlıya geçiş değerlendiricisi.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Canlıya geçmeden önce aday model canlı veya yakın-zamanlı veri üzerinde
  kullanıcı arayüzüne veya otomatik emir sistemine yansıtılmaksızın "gölge modunda" (shadow mode)
  izlenir.
- Gölge modunda üretilen tahminler kaydedilir, baseline modelle eşleştirilir,
  çıkarım gecikmesi (latency), belirsizlik aralıkları ve gerçekleşen getiri/fiyatlar
  ile karşılaştırılır.
- Asgari gözlem adedi, asgari gölge süresi, gecikme SLA eşiği, sıfır sessiz hata
  ve naif baseline'a karşı üstünlük sağlanmadan model canlıya terfi ettirilmez.
- Tüm çıktılar ve değerlendirme raporları SPK mevzuatına uygun yasal uyarı içerir.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from stock_analysis.indicators.trend import validate_compliant_text


class ShadowModeError(Exception):
    """Gölge modu genel istisnası."""


DEFAULT_SHADOW_MODE_DISCLAIMER = (
    "Bu gölge modu (shadow mode) değerlendirme raporu, modelin canlıya alınma öncesi "
    "teknik performans, gecikme ve doğruluk doğrulamasını içerir. Hiçbir çıktı "
    "kullanıcıya yatırım danışmanlığı, yönlendirme veya getiri vaadi olarak sunulamaz. "
    "Geçmiş veya gölge modu performansı gelecekteki sonuçların aynen gerçekleşeceğini ifade etmez. "
    "SPK mevzuatı uyarınca yatırım danışmanlığı kapsamında değildir."
)


class ShadowPromotionDecision(StrEnum):
    """Gölge modu terfi kararı."""

    PROCEED_TO_LIVE = "PROCEED_TO_LIVE"  # Tüm kriterler sağlandı, canlıya geçebilir
    EXTEND_SHADOW = "EXTEND_SHADOW"  # Süre veya gözlem sayısı henüz yetersiz
    REJECT = "REJECT"  # Hata oranı, SLA aşımı veya baseline altı performans


@dataclass(frozen=True)
class ShadowObservation:
    """Tekil bir gölge tahmin gözlemi."""

    observation_id: str
    model_version: str
    symbol: str
    timestamp: datetime
    horizon: str  # Ör. "1d", "5d", "20d"
    shadow_prediction: float
    baseline_prediction: float
    actual_value: float | None = None
    realized_at: datetime | None = None
    latency_ms: float = 0.0
    interval_lower: float | None = None
    interval_upper: float | None = None
    epistemic_uncertainty: float | None = None
    is_error: bool = False
    error_message: str | None = None

    def is_realized(self) -> bool:
        """Gerçekleşen değerin kaydedilip kaydedilmediğini döner."""
        return self.actual_value is not None

    def to_dict(self) -> dict[str, Any]:
        """Sözlük temsilini döner."""
        return {
            "observation_id": self.observation_id,
            "model_version": self.model_version,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "horizon": self.horizon,
            "shadow_prediction": self.shadow_prediction,
            "baseline_prediction": self.baseline_prediction,
            "actual_value": self.actual_value,
            "realized_at": self.realized_at.isoformat() if self.realized_at else None,
            "latency_ms": self.latency_ms,
            "interval_lower": self.interval_lower,
            "interval_upper": self.interval_upper,
            "epistemic_uncertainty": self.epistemic_uncertainty,
            "is_error": self.is_error,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ShadowModeConfig:
    """Gölge modu değerlendirme ve kabul eşikleri."""

    min_observations: int = 20  # Asgari tamamlanmış gözlem sayısı
    min_shadow_duration_days: int = 3  # Asgari gölge takip süresi (gün)
    max_allowed_latency_p95_ms: float = 200.0  # %95 gecikme tavanı
    max_allowed_error_rate: float = 0.01  # Azami kabul edilebilir hata/istisna oranı (%1)
    require_beat_baseline_mae: bool = True  # Model MAE <= Baseline MAE
    min_directional_accuracy: float = 0.50  # Asgari yön tutarlılığı
    min_interval_coverage: float = 0.65  # Tahmin aralığı gerçekleşme kapsaması


@dataclass(frozen=True)
class ShadowEvaluationReport:
    """Gölge modu değerlendirme çıktısı ve terfi kararı."""

    model_version: str
    evaluation_time: datetime
    total_observations: int
    realized_observations: int
    unrealized_observations: int
    shadow_duration_days: float
    model_mae: float | None
    baseline_mae: float | None
    model_rmse: float | None
    baseline_rmse: float | None
    model_directional_accuracy: float | None
    baseline_directional_accuracy: float | None
    interval_coverage: float | None
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    error_count: int
    error_rate: float
    decision: ShadowPromotionDecision
    rejection_reasons: list[str] = field(default_factory=list)
    pending_reasons: list[str] = field(default_factory=list)
    disclaimer: str = DEFAULT_SHADOW_MODE_DISCLAIMER

    def __post_init__(self) -> None:
        validate_compliant_text(self.disclaimer)

    def is_approved_for_live(self) -> bool:
        """Modelin canlıya çıkış onayı alıp almadığını bildirir."""
        return self.decision == ShadowPromotionDecision.PROCEED_TO_LIVE

    def to_dict(self) -> dict[str, Any]:
        """JSON serileştirilebilir sözlük formatı."""
        return {
            "model_version": self.model_version,
            "evaluation_time": self.evaluation_time.isoformat(),
            "decision": self.decision.value,
            "is_approved_for_live": self.is_approved_for_live(),
            "total_observations": self.total_observations,
            "realized_observations": self.realized_observations,
            "unrealized_observations": self.unrealized_observations,
            "shadow_duration_days": round(self.shadow_duration_days, 2),
            "model_mae": round(self.model_mae, 6) if self.model_mae is not None else None,
            "baseline_mae": round(self.baseline_mae, 6) if self.baseline_mae is not None else None,
            "model_rmse": round(self.model_rmse, 6) if self.model_rmse is not None else None,
            "baseline_rmse": (
                round(self.baseline_rmse, 6) if self.baseline_rmse is not None else None
            ),
            "model_directional_accuracy": (
                round(self.model_directional_accuracy, 4)
                if self.model_directional_accuracy is not None
                else None
            ),
            "baseline_directional_accuracy": (
                round(self.baseline_directional_accuracy, 4)
                if self.baseline_directional_accuracy is not None
                else None
            ),
            "interval_coverage": (
                round(self.interval_coverage, 4) if self.interval_coverage is not None else None
            ),
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "latency_p99_ms": round(self.latency_p99_ms, 2),
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "rejection_reasons": self.rejection_reasons,
            "pending_reasons": self.pending_reasons,
            "disclaimer": self.disclaimer,
        }


class ShadowModeTracker:
    """Gölge modu gözlemlerini kaydeden ve değerlendiren takipçi."""

    def __init__(
        self,
        model_version: str,
        config: ShadowModeConfig | None = None,
    ) -> None:
        if not model_version or not model_version.strip():
            raise ShadowModeError("Model sürüm adı boş olamaz.")
        self.model_version = model_version.strip()
        self.config = config or ShadowModeConfig()
        self._observations: dict[str, ShadowObservation] = {}

    def record_prediction(
        self,
        symbol: str,
        timestamp: datetime,
        horizon: str,
        shadow_prediction: float,
        baseline_prediction: float,
        latency_ms: float = 0.0,
        interval_lower: float | None = None,
        interval_upper: float | None = None,
        epistemic_uncertainty: float | None = None,
        observation_id: str | None = None,
        is_error: bool = False,
        error_message: str | None = None,
    ) -> ShadowObservation:
        """Yeni bir gölge tahmin gözlemi kaydeder (kullanıcıya gösterilmez)."""
        if not symbol or not symbol.strip():
            raise ShadowModeError("Hisse sembolü boş olamaz.")
        if latency_ms < 0:
            raise ShadowModeError("Gecikme süresi (latency) negatif olamaz.")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        obs_id = observation_id or f"obs_{uuid.uuid4().hex[:12]}"
        obs = ShadowObservation(
            observation_id=obs_id,
            model_version=self.model_version,
            symbol=symbol.strip().upper(),
            timestamp=timestamp,
            horizon=horizon,
            shadow_prediction=float(shadow_prediction),
            baseline_prediction=float(baseline_prediction),
            latency_ms=float(latency_ms),
            interval_lower=float(interval_lower) if interval_lower is not None else None,
            interval_upper=float(interval_upper) if interval_upper is not None else None,
            epistemic_uncertainty=(
                float(epistemic_uncertainty) if epistemic_uncertainty is not None else None
            ),
            is_error=is_error,
            error_message=error_message,
        )
        self._observations[obs_id] = obs
        return obs

    def resolve_observation(
        self,
        observation_id: str,
        actual_value: float,
        realized_at: datetime | None = None,
    ) -> ShadowObservation:
        """Gözlemlenen tahminin gerçekleşen gerçek değerini kaydeder."""
        if observation_id not in self._observations:
            raise ShadowModeError(f"Gözlem ID bulunamadı: {observation_id}")

        old_obs = self._observations[observation_id]
        realized_time = realized_at or datetime.now(UTC)
        if realized_time.tzinfo is None:
            realized_time = realized_time.replace(tzinfo=UTC)

        updated_obs = ShadowObservation(
            observation_id=old_obs.observation_id,
            model_version=old_obs.model_version,
            symbol=old_obs.symbol,
            timestamp=old_obs.timestamp,
            horizon=old_obs.horizon,
            shadow_prediction=old_obs.shadow_prediction,
            baseline_prediction=old_obs.baseline_prediction,
            actual_value=float(actual_value),
            realized_at=realized_time,
            latency_ms=old_obs.latency_ms,
            interval_lower=old_obs.interval_lower,
            interval_upper=old_obs.interval_upper,
            epistemic_uncertainty=old_obs.epistemic_uncertainty,
            is_error=old_obs.is_error,
            error_message=old_obs.error_message,
        )
        self._observations[observation_id] = updated_obs
        return updated_obs

    def get_observation(self, observation_id: str) -> ShadowObservation | None:
        """ID'ye göre tekil gözlemi döner."""
        return self._observations.get(observation_id)

    def list_observations(self) -> list[ShadowObservation]:
        """Kayıtlı tüm gözlemleri kronolojik sırada döner."""
        return sorted(self._observations.values(), key=lambda o: o.timestamp)

    def to_dataframe(self) -> pd.DataFrame:
        """Tüm gözlemleri pandas DataFrame olarak döner."""
        data = [obs.to_dict() for obs in self.list_observations()]
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data)

    def evaluate(self, current_time: datetime | None = None) -> ShadowEvaluationReport:
        """Gölge modu gözlemlerini analiz eder ve terfi karar raporu üretir."""
        eval_time = current_time or datetime.now(UTC)
        if eval_time.tzinfo is None:
            eval_time = eval_time.replace(tzinfo=UTC)

        all_obs = self.list_observations()
        total_count = len(all_obs)

        if total_count == 0:
            return ShadowEvaluationReport(
                model_version=self.model_version,
                evaluation_time=eval_time,
                total_observations=0,
                realized_observations=0,
                unrealized_observations=0,
                shadow_duration_days=0.0,
                model_mae=None,
                baseline_mae=None,
                model_rmse=None,
                baseline_rmse=None,
                model_directional_accuracy=None,
                baseline_directional_accuracy=None,
                interval_coverage=None,
                latency_p50_ms=0.0,
                latency_p95_ms=0.0,
                latency_p99_ms=0.0,
                error_count=0,
                error_rate=0.0,
                decision=ShadowPromotionDecision.EXTEND_SHADOW,
                pending_reasons=["Henüz hiç gölge modu tahmini kaydedilmedi."],
            )

        # Süre hesabı
        earliest_time = all_obs[0].timestamp
        duration_days = max(0.0, (eval_time - earliest_time).total_seconds() / 86400.0)

        # Hata sayısı ve oranı
        error_count = sum(1 for o in all_obs if o.is_error)
        error_rate = error_count / total_count if total_count > 0 else 0.0

        # Gecikme persentilleri
        latencies = [o.latency_ms for o in all_obs]
        p50_lat = float(np.percentile(latencies, 50))
        p95_lat = float(np.percentile(latencies, 95))
        p99_lat = float(np.percentile(latencies, 99))

        # Gerçekleşen (realized) gözlemler
        realized_obs = [o for o in all_obs if o.is_realized() and not o.is_error]
        realized_count = len(realized_obs)
        unrealized_count = total_count - realized_count

        model_mae: float | None = None
        baseline_mae: float | None = None
        model_rmse: float | None = None
        baseline_rmse: float | None = None
        model_dir_acc: float | None = None
        baseline_dir_acc: float | None = None
        interval_coverage: float | None = None

        if realized_count > 0:
            m_preds = np.array([o.shadow_prediction for o in realized_obs])
            b_preds = np.array([o.baseline_prediction for o in realized_obs])
            actuals = np.array([o.actual_value for o in realized_obs], dtype=float)

            # Hata metrikleri
            model_errors = m_preds - actuals
            base_errors = b_preds - actuals

            model_mae = float(np.mean(np.abs(model_errors)))
            baseline_mae = float(np.mean(np.abs(base_errors)))
            model_rmse = float(np.sqrt(np.mean(model_errors**2)))
            baseline_rmse = float(np.sqrt(np.mean(base_errors**2)))

            # Yön doğruluğu (sign agreement)
            m_dir_correct = np.sign(m_preds) == np.sign(actuals)
            b_dir_correct = np.sign(b_preds) == np.sign(actuals)
            model_dir_acc = float(np.mean(m_dir_correct))
            baseline_dir_acc = float(np.mean(b_dir_correct))

            # Aralık kapsaması (coverage)
            bounded_obs = [
                o
                for o in realized_obs
                if o.interval_lower is not None
                and o.interval_upper is not None
                and o.actual_value is not None
            ]
            if bounded_obs:
                covered = sum(
                    1
                    for o in bounded_obs
                    if (
                        o.interval_lower is not None
                        and o.actual_value is not None
                        and o.interval_lower <= o.actual_value
                    )
                    and (
                        o.interval_upper is not None
                        and o.actual_value is not None
                        and o.actual_value <= o.interval_upper
                    )
                )
                interval_coverage = covered / len(bounded_obs)

        # Karar mantığı
        rejection_reasons: list[str] = []
        pending_reasons: list[str] = []

        # 1. Hata oranı kontrolü
        if error_rate > self.config.max_allowed_error_rate:
            rejection_reasons.append(
                f"Hata oranı (%{error_rate * 100:.2f}) kabul edilebilir üst sınırı "
                f"(%{self.config.max_allowed_error_rate * 100:.2f}) aştı."
            )

        # 2. Gecikme (latency) SLA kontrolü
        if p95_lat > self.config.max_allowed_latency_p95_ms:
            rejection_reasons.append(
                f"Gecikme p95 değeri ({p95_lat:.1f} ms) SLA tavanını "
                f"({self.config.max_allowed_latency_p95_ms:.1f} ms) aştı."
            )

        # 3. Baseline karşılaştırması ve doğruluk
        if realized_count >= self.config.min_observations:
            if (
                self.config.require_beat_baseline_mae
                and model_mae is not None
                and baseline_mae is not None
            ):
                if model_mae > baseline_mae:
                    rejection_reasons.append(
                        f"Model MAE ({model_mae:.4f}), naif baseline MAE "
                        f"({baseline_mae:.4f}) değerini geçemedi."
                    )

            if model_dir_acc is not None and model_dir_acc < self.config.min_directional_accuracy:
                rejection_reasons.append(
                    f"Model yön doğruluğu (%{model_dir_acc * 100:.1f}) asgari eşiğin "
                    f"(%{self.config.min_directional_accuracy * 100:.1f}) altında kaldı."
                )

            if (
                interval_coverage is not None
                and interval_coverage < self.config.min_interval_coverage
            ):
                rejection_reasons.append(
                    f"Belirsizlik aralığı kapsaması (%{interval_coverage * 100:.1f}) hedef "
                    f"kapsamın (%{self.config.min_interval_coverage * 100:.1f}) altında kaldı."
                )
        else:
            pending_reasons.append(
                f"Gerçekleşen gözlem sayısı ({realized_count}) asgari gereken "
                f"({self.config.min_observations}) seviyesine henüz ulaşmadı."
            )

        # 4. Süre kontrolü
        if duration_days < self.config.min_shadow_duration_days:
            pending_reasons.append(
                f"Gölge takip süresi ({duration_days:.1f} gün), asgari süre "
                f"({self.config.min_shadow_duration_days} gün) dolana kadar sürmelidir."
            )

        # Nihai karar ataması
        if rejection_reasons:
            decision = ShadowPromotionDecision.REJECT
        elif pending_reasons:
            decision = ShadowPromotionDecision.EXTEND_SHADOW
        else:
            decision = ShadowPromotionDecision.PROCEED_TO_LIVE

        return ShadowEvaluationReport(
            model_version=self.model_version,
            evaluation_time=eval_time,
            total_observations=total_count,
            realized_observations=realized_count,
            unrealized_observations=unrealized_count,
            shadow_duration_days=duration_days,
            model_mae=model_mae,
            baseline_mae=baseline_mae,
            model_rmse=model_rmse,
            baseline_rmse=baseline_rmse,
            model_directional_accuracy=model_dir_acc,
            baseline_directional_accuracy=baseline_dir_acc,
            interval_coverage=interval_coverage,
            latency_p50_ms=p50_lat,
            latency_p95_ms=p95_lat,
            latency_p99_ms=p99_lat,
            error_count=error_count,
            error_rate=error_rate,
            decision=decision,
            rejection_reasons=rejection_reasons,
            pending_reasons=pending_reasons,
        )
