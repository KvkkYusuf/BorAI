"""Walk-forward ve expanding-window zaman serisi değerlendirme motoru.

FAZ-11 kapsamında finansal zaman serilerinde gelecek sızıntısını (look-ahead bias)
ve rejim kaymasını önleyen, gerçekçi doğrulama altyapısı sağlar:
- Genişleyen pencere (Expanding Window): Geçmiş veriyi biriktirerek ilerler.
- Kayan pencere (Rolling/Sliding Window): Sabit pencere ile rejim değişimine uyum sağlar.
- Purge / Embargo aralığı: Tahmin ufku örtüşmelerini engelleyen güvenli tampon.
- Katlama (Fold) sızıntısızlık denetimi: Train ve Test indekslerinin ayrıklığını doğrular.
- Çoklu katlama özeti: Ortalama, standart sapma, en kötü katlama ve aşırı öğrenme tespiti.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WindowingMethod(StrEnum):
    """Zaman serisi pencereleme yöntemi."""

    EXPANDING = "expanding"
    ROLLING = "rolling"


class WalkForwardLeakageError(ValueError):
    """Zaman serisi katlamalarında kronoloji veya embargo ihlali tespit edildiğinde fırlatılır."""


class WalkForwardConfigError(ValueError):
    """Pencereleme veya adım parametreleri geçersiz olduğunda fırlatılır."""


class FoldWindow(BaseModel):
    """Tek bir walk-forward doğrulama katlamasının sınırları ve indeksleri."""

    model_config = ConfigDict(frozen=True)

    fold_index: int
    train_indices: tuple[int, int]  # [start_idx, end_idx) yarı-açık aralık
    test_indices: tuple[int, int]  # [start_idx, end_idx) yarı-açık aralık
    embargo_size: int = 0
    train_start_date: str | None = None
    train_end_date: str | None = None
    test_start_date: str | None = None
    test_end_date: str | None = None

    @property
    def train_count(self) -> int:
        """Eğitim kümesindeki örneklem sayısı."""
        return self.train_indices[1] - self.train_indices[0]

    @property
    def test_count(self) -> int:
        """Test kümesindeki örneklem sayısı."""
        return self.test_indices[1] - self.test_indices[0]

    def assert_no_temporal_leakage(self) -> None:
        """Eğitim ve test aralıklarında zaman sızıntısı veya embargo ihlali denetler."""
        train_start, train_end = self.train_indices
        test_start, test_end = self.test_indices

        if train_start >= train_end:
            raise WalkForwardLeakageError(
                f"Katlama {self.fold_index}: Geçersiz train aralığı [{train_start}, {train_end})."
            )
        if test_start >= test_end:
            raise WalkForwardLeakageError(
                f"Katlama {self.fold_index}: Geçersiz test aralığı [{test_start}, {test_end})."
            )
        if train_end > test_start:
            raise WalkForwardLeakageError(
                f"Katlama {self.fold_index}: Gelecek sızıntısı tespit edildi! "
                f"Train bitiş ({train_end}) test başlangıçtan ({test_start}) sonra geliyor."
            )
        actual_gap = test_start - train_end
        if actual_gap < self.embargo_size:
            raise WalkForwardLeakageError(
                f"Katlama {self.fold_index}: Embargo ihlali! Gerekli embargo: {self.embargo_size}, "
                f"fiili boşluk: {actual_gap} (train_end={train_end}, test_start={test_start})."
            )


class WalkForwardConfig(BaseModel):
    """Walk-forward değerlendirme yapılandırması."""

    model_config = ConfigDict(frozen=True)

    windowing_method: WindowingMethod = WindowingMethod.EXPANDING
    min_train_size: int = Field(ge=10, description="Minimum eğitim penceresi boyutu")
    test_size: int = Field(ge=1, description="Her katlamadaki test penceresi boyutu")
    step_size: int | None = Field(
        default=None,
        ge=1,
        description="Pencereler arası ilerleme adımı. None ise test_size kullanılır.",
    )
    embargo_size: int = Field(
        default=0,
        ge=0,
        description="Train ile Test arasındaki etiket koruma boşluğu (bar sayısı).",
    )
    rolling_window_size: int | None = Field(
        default=None,
        ge=10,
        description="Rolling train penceresi boyutu. None ise min_train_size kullanılır.",
    )
    max_folds: int | None = Field(
        default=None,
        ge=1,
        description="Maksimum oluşturulacak katlama sayısı.",
    )


class FoldEvaluationResult(BaseModel):
    """Tek bir katlamanın değerlendirme metrikleri ve meta verileri."""

    model_config = ConfigDict(frozen=True)

    fold_index: int
    train_count: int
    test_count: int
    metrics: dict[str, float]
    train_period: tuple[str, str] | None = None
    test_period: tuple[str, str] | None = None


class WalkForwardReport(BaseModel):
    """Çoklu katlama değerlendirmesinin toplu analiz raporu."""

    model_config = ConfigDict(frozen=True)

    windowing_method: WindowingMethod
    n_folds: int
    total_samples: int
    embargo_size: int
    fold_results: list[FoldEvaluationResult]
    aggregated_metrics: dict[str, dict[str, float]]  # metric_name -> {mean, std, min, max}
    is_leak_free: bool = True
    overfitting_risk_warning: str | None = None


class WalkForwardValidator:
    """Finansal zaman serileri için Walk-Forward ve Expanding-Window doğrulama motoru."""

    def __init__(self, config: WalkForwardConfig) -> None:
        self.config = config

    def generate_folds(
        self,
        total_samples: int,
        timestamps: Sequence[date | datetime | str] | None = None,
    ) -> list[FoldWindow]:
        """Belirtilen örneklem sayısı ve yapılandırmaya göre kronolojik katlamalar üretir.

        Args:
            total_samples: Toplam veri satırı/bar sayısı.
            timestamps: Opsiyonel kronolojik zaman damgaları (uzunluk total_samples olmalıdır).

        Returns:
            Kronolojik ve sızıntısız FoldWindow listesi.
        """
        if timestamps is not None and len(timestamps) != total_samples:
            msg = (
                f"Zaman damgası sayısı ({len(timestamps)}) toplam "
                f"örneklem sayısıyla ({total_samples}) eşleşmiyor."
            )
            raise WalkForwardConfigError(msg)

        step = self.config.step_size or self.config.test_size
        min_required = self.config.min_train_size + self.config.embargo_size + self.config.test_size

        if total_samples < min_required:
            msg = (
                f"Toplam örneklem ({total_samples}) yetersiz. En az "
                f"min_train ({self.config.min_train_size}) + "
                f"embargo ({self.config.embargo_size}) + "
                f"test ({self.config.test_size}) = {min_required} gereklidir."
            )
            raise WalkForwardConfigError(msg)

        rolling_size = self.config.rolling_window_size or self.config.min_train_size
        folds: list[FoldWindow] = []
        current_fold = 0

        # İlk test başlangıcı: min_train_size + embargo_size
        test_start = self.config.min_train_size + self.config.embargo_size

        while test_start + self.config.test_size <= total_samples:
            test_end = test_start + self.config.test_size
            train_end = test_start - self.config.embargo_size

            if self.config.windowing_method == WindowingMethod.EXPANDING:
                train_start = 0
            else:  # ROLLING
                train_start = max(0, train_end - rolling_size)

            train_start_date: str | None = None
            train_end_date: str | None = None
            test_start_date: str | None = None
            test_end_date: str | None = None

            if timestamps is not None:
                train_start_date = str(timestamps[train_start])
                train_end_date = str(timestamps[train_end - 1])
                test_start_date = str(timestamps[test_start])
                test_end_date = str(timestamps[test_end - 1])

            fold = FoldWindow(
                fold_index=current_fold,
                train_indices=(train_start, train_end),
                test_indices=(test_start, test_end),
                embargo_size=self.config.embargo_size,
                train_start_date=train_start_date,
                train_end_date=train_end_date,
                test_start_date=test_start_date,
                test_end_date=test_end_date,
            )
            fold.assert_no_temporal_leakage()
            folds.append(fold)
            current_fold += 1

            if self.config.max_folds is not None and current_fold >= self.config.max_folds:
                break

            test_start += step

        if not folds:
            msg = f"Verilen parametrelerle hiçbir katlama oluşturulamadı (total={total_samples})."
            raise WalkForwardConfigError(msg)

        return folds

    def evaluate(
        self,
        total_samples: int,
        eval_fn: Callable[[FoldWindow], dict[str, float]],
        timestamps: Sequence[date | datetime | str] | None = None,
    ) -> WalkForwardReport:
        """Tüm katlamalar üzerinde değerlendirme fonksiyonunu çalıştırır ve toplu rapor üretir.

        Args:
            total_samples: Toplam veri satırı/bar sayısı.
            eval_fn: Her katlama için çalıştırılacak ve metrik sözlüğü döndürecek fonksiyon.
            timestamps: Opsiyonel kronolojik zaman damgaları.

        Returns:
            WalkForwardReport nesnesi.
        """
        folds = self.generate_folds(total_samples, timestamps=timestamps)
        fold_results: list[FoldEvaluationResult] = []

        all_metrics: dict[str, list[float]] = {}

        for fold in folds:
            metrics = eval_fn(fold)
            train_period = (
                (fold.train_start_date, fold.train_end_date)
                if fold.train_start_date and fold.train_end_date
                else None
            )
            test_period = (
                (fold.test_start_date, fold.test_end_date)
                if fold.test_start_date and fold.test_end_date
                else None
            )

            res = FoldEvaluationResult(
                fold_index=fold.fold_index,
                train_count=fold.train_count,
                test_count=fold.test_count,
                metrics=metrics,
                train_period=train_period,
                test_period=test_period,
            )
            fold_results.append(res)

            for k, v in metrics.items():
                if not math.isnan(v) and not math.isinf(v):
                    all_metrics.setdefault(k, []).append(v)

        # Metrik agregasyonu
        aggregated: dict[str, dict[str, float]] = {}
        for metric_name, values in all_metrics.items():
            if not values:
                continue
            mean_val = sum(values) / len(values)
            variance = (
                sum((x - mean_val) ** 2 for x in values) / len(values) if len(values) > 1 else 0.0
            )
            std_val = math.sqrt(variance)
            aggregated[metric_name] = {
                "mean": round(mean_val, 6),
                "std": round(std_val, 6),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
            }

        # Backtest aşırı öğrenme / yüksek varyans uyarısı
        overfitting_warning: str | None = None
        if "loss" in aggregated and len(folds) >= 3:
            loss_stat = aggregated["loss"]
            if loss_stat["mean"] > 0 and (loss_stat["std"] / loss_stat["mean"]) > 0.6:
                overfitting_warning = (
                    f"Yüksek katlama varyansı tespit edildi (Loss std/mean = "
                    f"{loss_stat['std'] / loss_stat['mean']:.2%}). "
                    "Farklı piyasa rejimlerinde kararsızlık veya aşırı öğrenme riski mevcuttur."
                )

        return WalkForwardReport(
            windowing_method=self.config.windowing_method,
            n_folds=len(folds),
            total_samples=total_samples,
            embargo_size=self.config.embargo_size,
            fold_results=fold_results,
            aggregated_metrics=aggregated,
            is_leak_free=True,
            overfitting_risk_warning=overfitting_warning,
        )


def assert_walk_forward_leak_free(folds: Sequence[FoldWindow]) -> None:
    """Tüm katlamaların bağımsızlığını ve kesin zamansal sızıntısızlığını doğrular."""
    for i, fold in enumerate(folds):
        fold.assert_no_temporal_leakage()
        if i > 0:
            prev_fold = folds[i - 1]
            if fold.test_indices[0] <= prev_fold.test_indices[0]:
                raise WalkForwardLeakageError(
                    f"Katlamalar kronolojik olarak ilerlemiyor! "
                    f"Katlama {i} test başlangıcı ({fold.test_indices[0]}) <= "
                    f"Katlama {i - 1} test başlangıcı ({prev_fold.test_indices[0]})."
                )
