"""Tahmin hedefi ufukları ve sonuç ayrıştırma motoru (FAZ-11).

Kural gereği (docs/phases/FAZ-11-pytorch-model.md):
- Hedef ufuklarını belirle (ör. 1 gün, 5 gün, 20 işlem günü).
- Sonuçları birbirine karıştırma (no cross-horizon contamination).
  Farklı ufukların tahminleri, metrikleri ve sinyalleri tek havuzda toplanamaz / karşılaştırılamaz.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stock_analysis.models.targets import TargetType


class ForecastHorizon(StrEnum):
    """Standart tahmin ufukları (işlem günü bazında)."""

    DAY_1 = "1d"  # 1 işlem günü (günlük / anlık reaksiyon)
    DAY_5 = "5d"  # 5 işlem günü (1 işlem haftası / swing)
    DAY_20 = "20d"  # 20 işlem günü (1 işlem ayı / orta vade)

    @property
    def steps(self) -> int:
        """Ufkun bar/adım sayısı."""
        if self == ForecastHorizon.DAY_1:
            return 1
        if self == ForecastHorizon.DAY_5:
            return 5
        if self == ForecastHorizon.DAY_20:
            return 20
        raise ValueError(f"Bilinmeyen ufuk: {self}")

    @property
    def label(self) -> str:
        """Kullanıcı dostu Türkçe etiket."""
        if self == ForecastHorizon.DAY_1:
            return "1 Gün (Günlük)"
        if self == ForecastHorizon.DAY_5:
            return "5 İşlem Günü (1 Hafta)"
        if self == ForecastHorizon.DAY_20:
            return "20 İşlem Günü (1 Ay)"
        return str(self.value)

    @classmethod
    def from_steps(cls, steps: int) -> ForecastHorizon:
        """Adım sayısına karşılık gelen standart ufku döndürür."""
        for horizon in cls:
            if horizon.steps == steps:
                return horizon
        supported = [f"{h.value} ({h.steps} adım)" for h in cls]
        raise ValueError(
            f"Desteklenmeyen tahmin ufkuna ({steps} adım) rastlandı. "
            f"Standart ufuklar: {', '.join(supported)}"
        )


class HorizonMismatchError(ValueError):
    """Farklı tahmin ufuklarının birbirine karıştırılması durumunda fırlatılan hata."""

    pass


@dataclass(frozen=True)
class HorizonEvaluationRecord:
    """Belirli bir tahmin ufkuna ait izole model değerlendirme kaydı."""

    horizon: ForecastHorizon
    model_name: str
    target_type: TargetType
    metrics: dict[str, float]
    sample_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sample_count <= 0:
            raise ValueError(f"Örneklem sayısı pozitif olmalıdır, verilen: {self.sample_count}")
        if not self.metrics:
            raise ValueError("En az bir değerlendirme metriği bulunmalıdır.")


class HorizonSeparationRegistry:
    """Ufuk bazında izole değerlendirme ve model kıyaslama kayıt defteri.

    FAZ-11 kuralı: Farklı ufukların sonuçları ASLA aynı havuza atılmaz veya birbirine karıştırılmaz.
    """

    def __init__(self) -> None:
        self._records_by_horizon: dict[ForecastHorizon, list[HorizonEvaluationRecord]] = {
            h: [] for h in ForecastHorizon
        }

    def register_evaluation(self, record: HorizonEvaluationRecord) -> None:
        """Model değerlendirme kaydını ilgili ufuk bölmesine ekler."""
        self._records_by_horizon[record.horizon].append(record)

    def get_records(self, horizon: ForecastHorizon) -> list[HorizonEvaluationRecord]:
        """Yalnızca belirtilen ufka ait kayıtları döndürür."""
        return list(self._records_by_horizon[horizon])

    def compare_models(
        self, horizon: ForecastHorizon, metric_name: str, higher_is_better: bool = True
    ) -> list[tuple[str, float]]:
        """Aynı ufuk içindeki modelleri belirli bir metriğe göre kıyaslar.

        Uyarı: Farklı ufuklar arası kıyaslama yapılamaz.
        """
        records = self.get_records(horizon)
        if not records:
            return []

        ranked: list[tuple[str, float]] = []
        for r in records:
            if metric_name in r.metrics:
                ranked.append((r.model_name, r.metrics[metric_name]))

        ranked.sort(key=lambda item: item[1], reverse=higher_is_better)
        return ranked

    @staticmethod
    def assert_no_cross_horizon_contamination(
        records: Sequence[HorizonEvaluationRecord],
    ) -> ForecastHorizon:
        """Kayıt kümesinin yalnızca tek bir ufka ait olduğunu doğrular.

        Farklı ufuklar birbirine karıştırılmışsa HorizonMismatchError fırlatır.
        """
        if not records:
            raise ValueError("Kayıt listesi boş olamaz.")

        first_horizon = records[0].horizon
        for record in records[1:]:
            if record.horizon != first_horizon:
                raise HorizonMismatchError(
                    f"FAZ-11 Kuralı İhlali: Farklı ufuklar birbirine karıştırılamaz! "
                    f"Beklenen: {first_horizon.value}, Karışan: {record.horizon.value} "
                    f"(Model: {record.model_name})"
                )
        return first_horizon
