"""Tahmin hedefleri ve etiket (target/label) üretim motoru.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Tahmin hedefini netleştir: Belirli ufukta getiri, yön sınıflandırması veya volatilite.
- Aynı anda birden fazla hedef karıştırılmaz ("tek model = tek net hedef").
- Gelecek verisi (t+H) yalnızca denetimli eğitim hedefi (y_t) olarak t indeksine etiketlenir;
  özelliklere sızdırılamaz ve son H adım NaN/None bırakılır.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from stock_analysis.models.horizons import ForecastHorizon


class TargetType(StrEnum):
    """Desteklenen tekil tahmin hedefi türleri."""

    LOG_RETURN = "log_return"  # ln(P_{t+H} / P_t)
    PERCENT_RETURN = "percent_return"  # (P_{t+H} - P_t) / P_t
    DIRECTION_CLASSIFICATION = "direction_classification"  # -1 (düşüş), 0 (nötr), +1 (artış)
    REALIZED_VOLATILITY = "realized_volatility"  # H günlük log getirilerin standart sapması


@dataclass(frozen=True)
class TargetConfig:
    """Tahmin hedefi yapılandırması.

    Aynı anda tek bir hedef türü (`target_type`) ve pozitif bir ufuk
    (`horizon_steps` veya `horizon`) tanımlanır.
    """

    target_type: TargetType
    horizon_steps: int = 1
    horizon: ForecastHorizon | None = None
    direction_threshold: float = 0.002  # %0.2 altı değişimler 0 (nötr) kabul edilir
    annualize_volatility: bool = False
    annualization_factor: float = 252.0  # BIST işlem günü sayısı

    def __post_init__(self) -> None:
        if self.horizon is not None:
            object.__setattr__(self, "horizon_steps", self.horizon.steps)
        elif self.horizon_steps in (1, 5, 20):
            try:
                object.__setattr__(self, "horizon", ForecastHorizon.from_steps(self.horizon_steps))
            except ValueError:
                pass

        if self.horizon_steps < 1:
            raise ValueError(f"horizon_steps en az 1 olmalıdır, verilen: {self.horizon_steps}")
        if self.direction_threshold < 0:
            raise ValueError("direction_threshold negatif olamaz")
        if self.annualization_factor <= 0:
            raise ValueError("annualization_factor pozitif olmalıdır")


@dataclass(frozen=True)
class TargetSeries:
    """Üretilen hedef serisi ve meta verileri."""

    target_type: TargetType
    horizon_steps: int
    values: tuple[float | None, ...]  # t indeksindeki hedef y_t (son horizon_steps adım None)
    valid_sample_count: int
    target_summary: dict[str, float]
    horizon: ForecastHorizon | None = None


class TargetGenerator:
    """Zaman serisi fiyatlarından denetimli öğrenme hedeflerini sızıntısız üreten motor."""

    def __init__(self, config: TargetConfig) -> None:
        self.config = config

    def compute_target(self, prices: Sequence[float]) -> TargetSeries:
        """Verilen kapanış fiyatları serisi için t anındaki ileriye dönük hedefi (y_t) hesaplar.

        Uyarı:
        y_t değeri t+horizon_steps anını temsil eder ve YALNIZCA model hedefi olarak kullanılır.
        Serinin son `horizon_steps` adımı henüz gerçekleşmediğinden None olarak işaretlenir.
        """
        n = len(prices)
        h = self.config.horizon_steps

        if n <= h:
            raise ValueError(f"Fiyat serisi uzunluğu ({n}), tahmin ufkundan ({h}) büyük olmalıdır.")

        for p in prices:
            if p <= 0 or math.isnan(p) or math.isinf(p):
                raise ValueError("Fiyatlar pozitif ve geçerli reel sayılar olmalıdır.")

        target_values: list[float | None] = []

        if self.config.target_type == TargetType.LOG_RETURN:
            for t in range(n - h):
                ret = math.log(prices[t + h] / prices[t])
                target_values.append(ret)
            for _ in range(h):
                target_values.append(None)

        elif self.config.target_type == TargetType.PERCENT_RETURN:
            for t in range(n - h):
                ret = (prices[t + h] - prices[t]) / prices[t]
                target_values.append(ret)
            for _ in range(h):
                target_values.append(None)

        elif self.config.target_type == TargetType.DIRECTION_CLASSIFICATION:
            thresh = self.config.direction_threshold
            for t in range(n - h):
                pct = (prices[t + h] - prices[t]) / prices[t]
                if pct > thresh:
                    target_values.append(1.0)
                elif pct < -thresh:
                    target_values.append(-1.0)
                else:
                    target_values.append(0.0)
            for _ in range(h):
                target_values.append(None)

        elif self.config.target_type == TargetType.REALIZED_VOLATILITY:
            # t ile t+h arasındaki 1 adımlık log getirilerin standart sapması
            factor = (
                math.sqrt(self.config.annualization_factor)
                if self.config.annualize_volatility
                else 1.0
            )
            for t in range(n - h):
                window_prices = prices[t : t + h + 1]
                log_rets = [
                    math.log(window_prices[i + 1] / window_prices[i])
                    for i in range(len(window_prices) - 1)
                ]
                if len(log_rets) > 1:
                    mean_ret = sum(log_rets) / len(log_rets)
                    variance = sum((r - mean_ret) ** 2 for r in log_rets) / (len(log_rets) - 1)
                    vol = math.sqrt(variance) * factor
                elif len(log_rets) == 1:
                    vol = abs(log_rets[0]) * factor
                else:
                    vol = 0.0
                target_values.append(vol)
            for _ in range(h):
                target_values.append(None)
        else:
            raise NotImplementedError(f"Desteklenmeyen hedef türü: {self.config.target_type}")

        valid_vals = [v for v in target_values if v is not None]
        summary: dict[str, float] = {}
        if valid_vals:
            summary["mean"] = sum(valid_vals) / len(valid_vals)
            summary["min"] = min(valid_vals)
            summary["max"] = max(valid_vals)

        return TargetSeries(
            target_type=self.config.target_type,
            horizon_steps=self.config.horizon_steps,
            values=tuple(target_values),
            valid_sample_count=len(valid_vals),
            target_summary=summary,
            horizon=self.config.horizon,
        )


def validate_single_target_constraint(targets: Sequence[TargetConfig]) -> TargetConfig:
    """FAZ-11 kuralı: 'Aynı anda çok hedef ekleme'.

    Bir model veya deney hattında aynı anda birden fazla hedef seçilmesini engeller.
    """
    if len(targets) != 1:
        raise ValueError(
            "FAZ-11 Kuralı: Aynı anda yalnızca 1 hedef yapılandırılabilir! "
            f"Verilen hedef sayısı: {len(targets)}"
        )
    return targets[0]
