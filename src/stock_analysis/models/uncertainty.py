"""Tahmin belirsizliği, güven aralıkları ve yön olasılığı motoru.

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/finans-hukuk.md):
- Finansal tahminler tek sayı (point estimate / çıplak hedef değer) olarak GÖSTERİLMEZ.
  Tek sayı vermek yanıltıcı kesinlik algısı yaratır ve SPK düzenlemelerine aykırıdır.
- Tahminler uygun olduğunda aralık (%80 ve %95 güven/tahmin bantları), kalibre edilmiş
  yön olasılığı P(artış)/P(azalış) ve belirsizlik derecesi (epistemik/aleatorik) ile sunulur.
- Çıplak tek sayı tahmin gösterimi varsayılan olarak engellenir (NakedPointEstimateDisallowedError).
- Her sunum ve rapor SPK uyumlu yasal uyarı taşır.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.indicators.trend import validate_compliant_text
from stock_analysis.models.provenance import (
    ForecastProvenance,
    MissingForecastProvenanceError,
)

DEFAULT_UNCERTAINTY_DISCLAIMER = (
    "Tahmin aralıkları ve yön olasılıkları istatistiksel modelleme çıktılarıdır; "
    "kesinlik veya getiri taahhüdü içermez. Piyasa koşullarına bağlı olarak gerçekleşen "
    "fiyatlar tahmin aralıklarının dışına çıkabilir. SPK mevzuatı uyarınca yatırım "
    "danışmanlığı kapsamında olmayıp bilgilendirme amaçlıdır."
)


class UncertaintyLevel(StrEnum):
    """Tahminin taşıdığı toplam belirsizlik seviyesi."""

    LOW = "low"  # Düşük varyans, dar güven aralığı
    MEDIUM = "medium"  # Tipik piyasa belirsizliği
    HIGH = "high"  # Yüksek oynaklık / rejim değişimi / geniş aralık
    EXTREME = "extreme"  # Aşırı belirsizlik; model öngörüsü güvenilmez


class Direction(StrEnum):
    """Olasılıksal yön sınıflandırması."""

    UP = "up"  # Pozitif getiri
    DOWN = "down"  # Negatif getiri
    NEUTRAL = "neutral"  # Eşik altı yatay hareket


class NakedPointEstimateDisallowedError(ValueError):
    """Tekil/çıplak sayı tahmininin kullanıcıya doğrudan gösterilmesi engellendi."""


class IntervalBound(BaseModel):
    """Belirli bir nominal güven seviyesi için tahmin aralığı (Prediction Interval)."""

    model_config = ConfigDict(frozen=True)

    confidence_level: float = Field(
        ge=0.50, le=0.999, description="Nominal güven seviyesi (ör. 0.80 veya 0.95)"
    )
    lower_bound: float = Field(description="Aralık alt sınırı")
    upper_bound: float = Field(description="Aralık üst sınırı")
    median_point: float = Field(description="Merkezi tahmin / medyan değer")
    interval_width: float = Field(ge=0.0, description="Üst sınır - Alt sınır")

    @classmethod
    def create(
        cls,
        *,
        confidence_level: float,
        lower_bound: float,
        upper_bound: float,
        median_point: float,
    ) -> IntervalBound:
        if lower_bound > upper_bound:
            raise ValueError(
                f"Alt sınır ({lower_bound}) üst sınırdan ({upper_bound}) büyük olamaz."
            )
        if not (lower_bound <= median_point <= upper_bound):
            # Sayısal yuvarlama toleransı
            if not (lower_bound - 1e-6 <= median_point <= upper_bound + 1e-6):
                raise ValueError(
                    f"Medyan ({median_point}) [alt, üst] aralığında yer almalıdır: "
                    f"[{lower_bound}, {upper_bound}]"
                )
        width = max(0.0, upper_bound - lower_bound)
        return cls(
            confidence_level=confidence_level,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            median_point=median_point,
            interval_width=width,
        )


class DirectionalProbabilities(BaseModel):
    """Kalibre edilmiş yönsel olasılık dağılımı (P(up), P(down), P(neutral))."""

    model_config = ConfigDict(frozen=True)

    prob_up: float = Field(ge=0.0, le=1.0, description="Artış olasılığı")
    prob_down: float = Field(ge=0.0, le=1.0, description="Azalış olasılığı")
    prob_neutral: float = Field(ge=0.0, le=1.0, description="Nötr/yatay olasılık")
    most_likely_direction: Direction = Field(description="En yüksek olasılıklı yön")
    directional_entropy: float = Field(
        ge=0.0, description="Shannon entropisi (belirsizlik ölçütü, nats)"
    )

    @classmethod
    def create(
        cls,
        prob_up: float,
        prob_down: float,
        prob_neutral: float = 0.0,
    ) -> DirectionalProbabilities:
        total = prob_up + prob_down + prob_neutral
        if total <= 0 or not math.isfinite(total):
            raise ValueError(f"Geçersiz olasılık toplamı: {total}")

        # Normalize et
        p_up = prob_up / total
        p_down = prob_down / total
        p_neu = prob_neutral / total

        # En olası yön
        probs = [(p_up, Direction.UP), (p_down, Direction.DOWN), (p_neu, Direction.NEUTRAL)]
        probs.sort(key=lambda x: x[0], reverse=True)
        most_likely = probs[0][1]

        # Shannon entropisi
        entropy = 0.0
        for p in (p_up, p_down, p_neu):
            if p > 1e-12:
                entropy -= p * math.log(p)

        return cls(
            prob_up=round(p_up, 4),
            prob_down=round(p_down, 4),
            prob_neutral=round(p_neu, 4),
            most_likely_direction=most_likely,
            directional_entropy=round(entropy, 4),
        )


class UncertaintyBreakdown(BaseModel):
    """Tahmin belirsizliğinin ayrıştırılmış bileşenleri."""

    model_config = ConfigDict(frozen=True)

    aleatoric_std: float = Field(
        ge=0.0, description="Aleatorik belirsizlik (veri/piyasa gürültüsü standart sapması)"
    )
    epistemic_std: float = Field(
        ge=0.0, description="Epistemik belirsizlik (model/parametre belirsizliği standart sapması)"
    )
    total_std: float = Field(
        ge=0.0, description="Bileşik toplam standart sapma sqrt(ale^2 + epi^2)"
    )
    level: UncertaintyLevel = Field(description="Kategorik belirsizlik seviyesi")


class UncertaintyForecast(BaseModel):
    """Tek sayı yerine aralık, olasılık ve belirsizlik bilgisi içeren tahmin nesnesi."""

    model_config = ConfigDict(frozen=True)

    point_forecast: float = Field(description="Merkezi nokta tahmin (medyan / beklenen getiri)")
    intervals: list[IntervalBound] = Field(
        min_length=1, description="Farklı güven seviyelerindeki tahmin aralıkları (ör. %80, %95)"
    )
    probabilities: DirectionalProbabilities = Field(description="Kalibre edilmiş yön olasılıkları")
    uncertainty: UncertaintyBreakdown = Field(description="Belirsizlik ayrıştırması")
    disclaimer: str = Field(description="SPK uyumlu yasal uyarı")
    allow_naked_point: bool = Field(
        default=False,
        description="Çıplak tek sayının aralıksız doğrudan gösterimine izin verilip verilmediği",
    )
    provenance: ForecastProvenance | None = Field(
        default=None,
        description="Tahmini üreten modelin sürüm, eğitim ve özellik meta verisi (FAZ-11)",
    )

    def get_provenance(self) -> ForecastProvenance:
        """Tahmin köken bilgisini döner; köken yoksa MissingForecastProvenanceError fırlatır."""
        if self.provenance is None:
            raise MissingForecastProvenanceError(
                "FAZ-11 Kuralı: Model sürümü, eğitim tarihi, veri aralığı ve özellik seti "
                "olmadan tahmin denetlenemez veya yayınlanamaz."
            )
        return self.provenance

    def with_provenance(self, provenance: ForecastProvenance) -> UncertaintyForecast:
        """Mevcut tahmine model köken bilgisini bağlayarak yeni nesne döner."""
        return self.model_copy(update={"provenance": provenance})

    def get_naked_point_estimate(self) -> float:
        """Çıplak nokta tahminini okur; ancak koruma açıksa yasaklar."""
        if not self.allow_naked_point:
            raise NakedPointEstimateDisallowedError(
                "FAZ-11 & SPK Kuralı: Finansal tahminler tekil çıplak sayı olarak sunulamaz. "
                "Lütfen aralık ('intervals') veya biçimlendirilmiş metin kullanın."
            )
        return self.point_forecast

    def get_interval(self, confidence_level: float) -> IntervalBound:
        """İstenen güven düzeyine en yakın tahmin aralığını döner."""
        if not self.intervals:
            raise ValueError("Kayıtlı tahmin aralığı bulunmuyor.")
        best = min(self.intervals, key=lambda b: abs(b.confidence_level - confidence_level))
        return best

    def format_display_text(
        self,
        percentage_mode: bool = True,
        include_provenance: bool = True,
    ) -> str:
        """Kullanıcı arayüzü ve raporlama için zenginleştirilmiş, aralıklı metin üretir."""
        # 80 ve 95 güven aralıklarını ara
        bound_80 = next(
            (b for b in self.intervals if abs(b.confidence_level - 0.80) < 0.05),
            self.intervals[0],
        )
        scale = 100.0 if percentage_mode else 1.0
        unit = "%" if percentage_mode else ""

        pt_str = f"{unit}{self.point_forecast * scale:+.2f}"
        low_str = f"{unit}{bound_80.lower_bound * scale:+.2f}"
        high_str = f"{unit}{bound_80.upper_bound * scale:+.2f}"
        conf_pct = int(round(bound_80.confidence_level * 100))

        p_up_str = f"%{self.probabilities.prob_up * 100:.1f}"
        p_down_str = f"%{self.probabilities.prob_down * 100:.1f}"

        level_tr = {
            UncertaintyLevel.LOW: "Düşük",
            UncertaintyLevel.MEDIUM: "Orta",
            UncertaintyLevel.HIGH: "Yüksek",
            UncertaintyLevel.EXTREME: "Çok Yüksek / Kritik",
        }.get(self.uncertainty.level, "Belirsiz")

        prov_str = ""
        if include_provenance and self.provenance is not None:
            prov_str = f" | {self.provenance.to_display_summary()}"

        text = (
            f"Tahmin Aralığı [{conf_pct}% Güven]: [{low_str} ile {high_str}] "
            f"(Merkez: {pt_str}) | Yön Olasılığı: {p_up_str} Artış, {p_down_str} Azalış | "
            f"Belirsizlik Seviyesi: {level_tr}{prov_str}. {self.disclaimer}"
        )
        validate_compliant_text(text)
        return text


# ---------------------------------------------------------------------------
# Belirsizlik ve Aralık Hesaplama Motorları
# ---------------------------------------------------------------------------


class ConformalIntervalCalibrator:
    """Dağılım varsayımı yapmayan Konformal Tahmin (Conformal Prediction) kalibratörü.

    Tarihsel kalibrasyon/artık (residual) verilerini kullanarak sonlu örneklemde
    teorik kapsama garantisi sağlayan dağılımsız simetrik veya asimetrik aralıklar üretir.
    """

    def __init__(self, calibration_residuals: Sequence[float]) -> None:
        if len(calibration_residuals) < 5:
            raise ValueError(
                "Konformal kalibrasyon için en az 5 artık gereklidir, verilen: "
                f"{len(calibration_residuals)}"
            )
        self.abs_residuals = sorted(abs(r) for r in calibration_residuals)
        self.n = len(self.abs_residuals)

    def compute_interval_margin(self, confidence_level: float) -> float:
        """İstenen 1 - alpha nominal güven seviyesi için konformal marjı q hesaplar."""
        if not (0.50 <= confidence_level < 1.0):
            raise ValueError("confidence_level 0.50 ile 1.0 arasında olmalıdır.")

        # Konformal kuantil formülü: ceil((n + 1) * confidence_level) / n
        k = math.ceil((self.n + 1) * confidence_level)
        index = min(max(k - 1, 0), self.n - 1)
        return self.abs_residuals[index]

    def create_interval(self, point_forecast: float, confidence_level: float) -> IntervalBound:
        margin = self.compute_interval_margin(confidence_level)
        return IntervalBound.create(
            confidence_level=confidence_level,
            lower_bound=point_forecast - margin,
            upper_bound=point_forecast + margin,
            median_point=point_forecast,
        )


class EnsembleUncertaintyEstimator:
    """Model topluluğu (Ensemble) veya MC Dropout çıktılarından belirsizlik ayrıştırma."""

    def __init__(
        self,
        volatility_threshold_high: float = 0.03,
        volatility_threshold_extreme: float = 0.06,
    ) -> None:
        self.vol_high = volatility_threshold_high
        self.vol_extreme = volatility_threshold_extreme

    def estimate_uncertainty(
        self,
        ensemble_predictions: Sequence[float],
        aleatoric_std: float = 0.01,
    ) -> UncertaintyBreakdown:
        if len(ensemble_predictions) < 2:
            raise ValueError("Epistemik varyans hesabı için en az 2 topluluk tahmini gereklidir.")
        if aleatoric_std < 0:
            raise ValueError("aleatoric_std negatif olamaz.")

        m = len(ensemble_predictions)
        mean_pred = sum(ensemble_predictions) / m
        epistemic_var = sum((p - mean_pred) ** 2 for p in ensemble_predictions) / (m - 1)
        epistemic_std = math.sqrt(epistemic_var)

        total_var = (aleatoric_std**2) + epistemic_var
        total_std = math.sqrt(total_var)

        if total_std >= self.vol_extreme:
            level = UncertaintyLevel.EXTREME
        elif total_std >= self.vol_high:
            level = UncertaintyLevel.HIGH
        elif total_std >= (self.vol_high / 2):
            level = UncertaintyLevel.MEDIUM
        else:
            level = UncertaintyLevel.LOW

        return UncertaintyBreakdown(
            aleatoric_std=round(aleatoric_std, 6),
            epistemic_std=round(epistemic_std, 6),
            total_std=round(total_std, 6),
            level=level,
        )


class DirectionalProbabilityCalibrator:
    """Sürekli getiri dağılımından (mu, sigma) kalibre yön olasılığı üreten dönüştürücü."""

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Standart normal kümülatif dağılım fonksiyonu Phi(x)."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    @classmethod
    def calculate_probabilities(
        cls,
        expected_return: float,
        total_std: float,
        threshold: float = 0.002,  # %0.2 altındaki mutlak değişimler yatay kabul edilir
    ) -> DirectionalProbabilities:
        if total_std <= 0:
            # Sıfır varyans uç durumu
            if expected_return > threshold:
                return DirectionalProbabilities.create(1.0, 0.0, 0.0)
            elif expected_return < -threshold:
                return DirectionalProbabilities.create(0.0, 1.0, 0.0)
            else:
                return DirectionalProbabilities.create(0.0, 0.0, 1.0)

        # z-skorları
        # P(R > threshold) = 1 - Phi((threshold - mu) / sigma)
        z_up = (threshold - expected_return) / total_std
        prob_up = max(0.0, min(1.0, 1.0 - cls._normal_cdf(z_up)))

        # P(R < -threshold) = Phi((-threshold - mu) / sigma)
        z_down = (-threshold - expected_return) / total_std
        prob_down = max(0.0, min(1.0, cls._normal_cdf(z_down)))

        prob_neutral = max(0.0, 1.0 - (prob_up + prob_down))

        return DirectionalProbabilities.create(
            prob_up=prob_up,
            prob_down=prob_down,
            prob_neutral=prob_neutral,
        )


def build_uncertainty_forecast(
    *,
    point_forecast: float,
    aleatoric_std: float,
    ensemble_predictions: Sequence[float] | None = None,
    calibration_residuals: Sequence[float] | None = None,
    confidence_levels: Sequence[float] = (0.80, 0.95),
    direction_threshold: float = 0.002,
    disclaimer: str = DEFAULT_UNCERTAINTY_DISCLAIMER,
    allow_naked_point: bool = False,
    provenance: ForecastProvenance | None = None,
) -> UncertaintyForecast:
    """Tek sayı yerine aralık, olasılık ve belirsizlik nesnesi oluşturan fabrika fonksiyonu."""
    validate_compliant_text(disclaimer)

    # 1. Epistemik ve aleatorik belirsizlik hesapla
    if ensemble_predictions and len(ensemble_predictions) >= 2:
        estimator = EnsembleUncertaintyEstimator()
        breakdown = estimator.estimate_uncertainty(
            ensemble_predictions=ensemble_predictions,
            aleatoric_std=aleatoric_std,
        )
    else:
        total_std = max(1e-6, aleatoric_std)
        if total_std >= 0.06:
            level = UncertaintyLevel.EXTREME
        elif total_std >= 0.03:
            level = UncertaintyLevel.HIGH
        elif total_std >= 0.015:
            level = UncertaintyLevel.MEDIUM
        else:
            level = UncertaintyLevel.LOW

        breakdown = UncertaintyBreakdown(
            aleatoric_std=round(aleatoric_std, 6),
            epistemic_std=0.0,
            total_std=round(total_std, 6),
            level=level,
        )

    # 2. Aralıkları hesapla (Konformal varsa konformal, yoksa normal dağılım z-skoru)
    intervals: list[IntervalBound] = []
    if calibration_residuals and len(calibration_residuals) >= 5:
        conformal = ConformalIntervalCalibrator(calibration_residuals)
        for cl in confidence_levels:
            intervals.append(conformal.create_interval(point_forecast, cl))
    else:
        # Parametrik normal yaklaşım z-skorları
        z_map = {0.80: 1.28155, 0.90: 1.64485, 0.95: 1.95996, 0.99: 2.57583}
        for cl in confidence_levels:
            z = z_map.get(round(cl, 2), 1.96)
            margin = z * breakdown.total_std
            intervals.append(
                IntervalBound.create(
                    confidence_level=cl,
                    lower_bound=point_forecast - margin,
                    upper_bound=point_forecast + margin,
                    median_point=point_forecast,
                )
            )

    # 3. Yön olasılıkları
    probabilities = DirectionalProbabilityCalibrator.calculate_probabilities(
        expected_return=point_forecast,
        total_std=breakdown.total_std,
        threshold=direction_threshold,
    )

    return UncertaintyForecast(
        point_forecast=point_forecast,
        intervals=intervals,
        probabilities=probabilities,
        uncertainty=breakdown,
        disclaimer=disclaimer,
        allow_naked_point=allow_naked_point,
        provenance=provenance,
    )
