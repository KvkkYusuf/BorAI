"""PyTorch ve makine öğrenmesi modelleri için naif kıyas (baseline) modelleri.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Hiçbir karmaşık model, naif baseline'ı (son değer, tarihsel ortalama, basit momentum)
  geçmeden yayına alınamaz veya tahmin üretiminde kullanılamaz.
- Tüm tahminler look-ahead bias olmadan yalnızca t anındaki geçmiş veriye dayanır.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BaselineForecastResult:
    """Tek bir naif modelin tahmin çıktısı."""

    baseline_name: str
    symbol: str
    horizon_steps: int
    point_predictions: list[float]
    directional_predictions: list[int]  # -1 (düşüş), 0 (nötr), 1 (artış)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BaselineEvaluationMetric:
    """Model veya baseline performans metrikleri."""

    mae: float
    rmse: float
    directional_accuracy: float  # [0.0, 1.0]
    sample_count: int


@dataclass(frozen=True)
class ModelBenchmarkReport:
    """Bir modelin naif baseline ile karşılaştırma karnesi."""

    model_name: str
    baseline_name: str
    model_metrics: BaselineEvaluationMetric
    baseline_metrics: BaselineEvaluationMetric
    relative_mae_improvement: float  # (base_mae - model_mae) / base_mae
    relative_rmse_improvement: float  # (base_rmse - model_rmse) / base_rmse
    beats_baseline: bool
    forecast_approved: bool
    rejection_reason: str | None = None


class BaseForecastBaseline(ABC):
    """Naif kıyas modelleri için temel soyut sınıf."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def predict(
        self,
        prices: Sequence[float],
        horizon: int = 1,
        symbol: str = "UNKNOWN",
    ) -> BaselineForecastResult:
        """Geçmiş fiyat serisine dayanarak gelecek adımlar için tahmin üretir.

        Args:
            prices: Kronolojik olarak sıralı geçmiş fiyat dizisi [P_1, ..., P_t].
            horizon: İleriye dönük tahmin adım sayısı (>= 1).
            symbol: Finansal enstrüman sembolü.

        Returns:
            BaselineForecastResult: Nokta tahminleri ve yön beklentisi.
        """
        ...

    def _validate_inputs(self, prices: Sequence[float], horizon: int, min_points: int = 1) -> None:
        if horizon < 1:
            raise ValueError(f"Tahmin ufku (horizon) en az 1 olmalıdır, verilen: {horizon}")
        if len(prices) < min_points:
            raise ValueError(
                f"{self.name} için en az {min_points} adet geçmiş fiyat verisi gereklidir, "
                f"verilen: {len(prices)}"
            )
        for p in prices:
            if math.isnan(p) or math.isinf(p) or p <= 0:
                raise ValueError(
                    f"Geçmiş fiyatlar geçerli ve pozitif olmalıdır, geçersiz değer: {p}"
                )


class LastValueBaseline(BaseForecastBaseline):
    """Son Değer (Persistence / Random Walk) Naif Modeli.

    Finansal zaman serilerinde en güçlü temel kıyas noktasıdır.
    Gelecek tüm adımlarda fiyatın son gözlemlenen fiyata eşit kalacağını (getiri=0)
    varsayar: P_{t+h} = P_t.
    """

    def __init__(self) -> None:
        super().__init__(name="last_value")

    def predict(
        self,
        prices: Sequence[float],
        horizon: int = 1,
        symbol: str = "UNKNOWN",
    ) -> BaselineForecastResult:
        self._validate_inputs(prices, horizon, min_points=1)
        last_price = float(prices[-1])

        point_preds = [last_price for _ in range(horizon)]
        directional_preds = [0 for _ in range(horizon)]

        return BaselineForecastResult(
            baseline_name=self.name,
            symbol=symbol,
            horizon_steps=horizon,
            point_predictions=point_preds,
            directional_predictions=directional_preds,
            metadata={"last_observed_price": last_price},
        )


class HistoricalMeanBaseline(BaseForecastBaseline):
    """Tarihsel Ortalama Getiri / Hareketli Ortalama Naif Modeli.

    Geçmiş pencere (window) içindeki günlük basit yüzdesel getirilerin ortalamasını (mu)
    hesaplar ve son fiyata bileşik uygulayarak projeksiyon yapar:
    P_{t+h} = P_t * (1 + mu)^h
    """

    def __init__(self, window: int | None = 20) -> None:
        super().__init__(name=f"historical_mean_w{window if window else 'all'}")
        if window is not None and window < 2:
            raise ValueError(
                f"Pencere boyutu en az 2 olmalıdır veya None olmalıdır, verilen: {window}"
            )
        self.window = window

    def predict(
        self,
        prices: Sequence[float],
        horizon: int = 1,
        symbol: str = "UNKNOWN",
    ) -> BaselineForecastResult:
        min_required = (self.window + 1) if self.window is not None else 2
        self._validate_inputs(prices, horizon, min_points=min_required)

        relevant_prices = prices[-self.window :] if self.window is not None else prices
        # Günlük getiriler: r_i = (P_i - P_{i-1}) / P_{i-1}
        returns = [
            (relevant_prices[i] - relevant_prices[i - 1]) / relevant_prices[i - 1]
            for i in range(1, len(relevant_prices))
        ]
        mean_return = sum(returns) / len(returns)

        last_price = float(prices[-1])
        point_preds: list[float] = []
        directional_preds: list[int] = []

        direction = 1 if mean_return > 1e-6 else (-1 if mean_return < -1e-6 else 0)

        for step in range(1, horizon + 1):
            projected = last_price * math.pow(1.0 + mean_return, step)
            point_preds.append(round(projected, 6))
            directional_preds.append(direction)

        return BaselineForecastResult(
            baseline_name=self.name,
            symbol=symbol,
            horizon_steps=horizon,
            point_predictions=point_preds,
            directional_predictions=directional_preds,
            metadata={
                "last_observed_price": last_price,
                "mean_return": mean_return,
                "window": self.window,
            },
        )


class SimpleMomentumBaseline(BaseForecastBaseline):
    """Basit Momentum Naif Modeli.

    Son k periyotluk momentumu (k-bar getiri oranı) baz alır.
    Pozitif momentum yukarı, negatif momentum aşağı yönlü hareket projeksiyonu yapar.
    Adım başına momentum oranı: r_step = R_k / k
    P_{t+h} = P_t * (1 + r_step)^h
    """

    def __init__(self, lookback_k: int = 5) -> None:
        super().__init__(name=f"simple_momentum_k{lookback_k}")
        if lookback_k < 1:
            raise ValueError(f"Lookback periyodu en az 1 olmalıdır, verilen: {lookback_k}")
        self.lookback_k = lookback_k

    def predict(
        self,
        prices: Sequence[float],
        horizon: int = 1,
        symbol: str = "UNKNOWN",
    ) -> BaselineForecastResult:
        self._validate_inputs(prices, horizon, min_points=self.lookback_k + 1)

        last_price = float(prices[-1])
        base_k_price = float(prices[-(self.lookback_k + 1)])

        total_momentum = (last_price - base_k_price) / base_k_price
        step_return = total_momentum / self.lookback_k

        # Aşırı uç momentum projeksiyonunu korumak için sınır (-%50 ile +%50 arası)
        clamped_step_return = max(min(step_return, 0.5), -0.5)

        point_preds: list[float] = []
        directional_preds: list[int] = []

        direction = 1 if total_momentum > 1e-6 else (-1 if total_momentum < -1e-6 else 0)

        for step in range(1, horizon + 1):
            projected = last_price * math.pow(1.0 + clamped_step_return, step)
            point_preds.append(round(projected, 6))
            directional_preds.append(direction)

        return BaselineForecastResult(
            baseline_name=self.name,
            symbol=symbol,
            horizon_steps=horizon,
            point_predictions=point_preds,
            directional_predictions=directional_preds,
            metadata={
                "last_observed_price": last_price,
                "total_momentum": total_momentum,
                "step_return": step_return,
                "lookback_k": self.lookback_k,
            },
        )


def compute_metrics(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    last_known_prices: Sequence[float] | None = None,
) -> BaselineEvaluationMetric:
    """MAE, RMSE ve Yön Doğruluğu (Directional Accuracy) hesaplar."""
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"Gerçek ({len(y_true)}) ve tahmin ({len(y_pred)}) dizi uzunlukları eşit olmalıdır."
        )
    n = len(y_true)
    if n == 0:
        raise ValueError("Metrik hesaplamak için en az bir veri noktası gereklidir.")

    abs_errors = [abs(t - p) for t, p in zip(y_true, y_pred, strict=True)]
    sq_errors = [(t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True)]

    mae = sum(abs_errors) / n
    rmse = math.sqrt(sum(sq_errors) / n)

    # Directional accuracy
    if last_known_prices is not None and len(last_known_prices) == n:
        correct_direction = 0
        valid_dir_steps = 0
        for t, p, lk in zip(y_true, y_pred, last_known_prices, strict=True):
            actual_dir = 1 if t > lk else (-1 if t < lk else 0)
            pred_dir = 1 if p > lk else (-1 if p < lk else 0)
            if actual_dir != 0:
                valid_dir_steps += 1
                if pred_dir == actual_dir:
                    correct_direction += 1
        da = (correct_direction / valid_dir_steps) if valid_dir_steps > 0 else 0.5
    else:
        # Ardışık adımların yön farkı
        correct_direction = 0
        valid_dir_steps = 0
        for i in range(1, n):
            actual_diff = y_true[i] - y_true[i - 1]
            pred_diff = y_pred[i] - y_pred[i - 1]
            actual_dir = 1 if actual_diff > 0 else (-1 if actual_diff < 0 else 0)
            pred_dir = 1 if pred_diff > 0 else (-1 if pred_diff < 0 else 0)
            if actual_dir != 0:
                valid_dir_steps += 1
                if pred_dir == actual_dir:
                    correct_direction += 1
        da = (correct_direction / valid_dir_steps) if valid_dir_steps > 0 else 0.5

    return BaselineEvaluationMetric(
        mae=round(mae, 6),
        rmse=round(rmse, 6),
        directional_accuracy=round(da, 4),
        sample_count=n,
    )


def evaluate_forecast_against_baseline(
    y_true: Sequence[float],
    y_model: Sequence[float],
    y_baseline: Sequence[float],
    last_known_prices: Sequence[float] | None = None,
    model_name: str = "CandidateModel",
    baseline_name: str = "last_value",
) -> ModelBenchmarkReport:
    """Bir modelin performansını naif baseline ile karşılaştırır ve yayın onayını denetler.

    Kural:
    - Model, naif baseline'a göre MAE ve RMSE açısından daha düşük (daha iyi) hata üretmeli.
    - Modelin yön doğruluğu naif baseline'dan düşük olmamalıdır.
    - Kriterler sağlanmıyorsa beats_baseline=False ve forecast_approved=False döner.
    """
    model_met = compute_metrics(y_true, y_model, last_known_prices)
    base_met = compute_metrics(y_true, y_baseline, last_known_prices)

    rel_mae = (base_met.mae - model_met.mae) / base_met.mae if base_met.mae > 0 else 0.0
    rel_rmse = (base_met.rmse - model_met.rmse) / base_met.rmse if base_met.rmse > 0 else 0.0

    # Model hem MAE'de hem RMSE'de baseline'dan iyi olmalıdır
    mae_passed = model_met.mae < base_met.mae
    rmse_passed = model_met.rmse < base_met.rmse
    da_passed = model_met.directional_accuracy >= base_met.directional_accuracy

    reasons: list[str] = []
    if not mae_passed:
        reasons.append(
            f"Model MAE ({model_met.mae}) baseline MAE'den ({base_met.mae}) yüksek/eşit."
        )
    if not rmse_passed:
        reasons.append(
            f"Model RMSE ({model_met.rmse}) baseline RMSE'den ({base_met.rmse}) yüksek/eşit."
        )
    if not da_passed:
        reasons.append(
            f"Model yön doğruluğu (%{model_met.directional_accuracy * 100:.1f}) "
            f"baseline'dan (%{base_met.directional_accuracy * 100:.1f}) düşük."
        )

    beats_baseline = mae_passed and rmse_passed and da_passed
    forecast_approved = beats_baseline

    return ModelBenchmarkReport(
        model_name=model_name,
        baseline_name=baseline_name,
        model_metrics=model_met,
        baseline_metrics=base_met,
        relative_mae_improvement=round(rel_mae, 4),
        relative_rmse_improvement=round(rel_rmse, 4),
        beats_baseline=beats_baseline,
        forecast_approved=forecast_approved,
        rejection_reason="; ".join(reasons) if reasons else None,
    )


# Alias for roadmap & prompt standardization
NaiveMomentumBaseline = SimpleMomentumBaseline

