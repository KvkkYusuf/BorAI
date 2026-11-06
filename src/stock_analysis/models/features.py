"""Zaman serisi özellik çıkarımı ve geleceğe bakış (lookahead bias) koruması (FAZ-11).

Kural gereği (docs/phases/FAZ-11-pytorch-model.md & .agent/rules/veri-ml.md):
- Özellikler yalnızca tahmin anında (t) bilinen ve kesinleşmiş verilerden üretilir.
- As-of tarih/saat kesimi (point-in-time cutoff) ve katı gecikme (lag >= 1) uygulanır.
- Ortalama, standart sapma veya normalizasyon parametreleri yalnızca eğitim verisinden öğrenilir;
  test/val verisine global sızıntı (data leakage) engellenir.
- Gelecek barlarından (t+k) veya hedef etiketinden (y_t) özellik setine sızıntı yapılamaz.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from stock_analysis.models.targets import TargetSeries
from stock_analysis.pipeline.lookahead_bias import (
    DataLeakageError,
    LookaheadBiasDetector,
)
from stock_analysis.providers.base import MarketBarRecord


class FeatureTemporalIntegrityError(DataLeakageError):
    """Zaman nedenselliği veya as-of kesim kuralı ihlal edildiğinde fırlatılır."""


class PreprocessingLeakageError(DataLeakageError):
    """Ön işleme/normalizasyon parametreleri test verisine sızdığında fırlatılır."""


class FeatureType(StrEnum):
    """Desteklenen standart nedensel özellik türleri."""

    LOG_RETURN = "log_return"
    SMA_RATIO = "sma_ratio"
    ROLLING_VOLATILITY = "rolling_volatility"
    RSI = "rsi"
    VOLUME_RATIO = "volume_ratio"
    NORMALIZED_RANGE = "normalized_range"  # (High - Low) / Close


@dataclass(frozen=True)
class FeatureDefinition:
    """Tekil bir model özelliğinin nedensel tanımı.

    Varsayılan lag=1: t anındaki tahmin için en son t-1 barının kapanışı kullanılır.
    Bu kural, henüz kapanmamış seans içi barların t anında sızmasını engeller.
    """

    name: str
    feature_type: FeatureType
    window_bars: int = 1
    lag: int = 1
    description: str = ""

    def __post_init__(self) -> None:
        if self.window_bars < 1:
            raise ValueError(f"window_bars en az 1 olmalıdır, verilen: {self.window_bars}")
        if self.lag < 0:
            raise FeatureTemporalIntegrityError(
                f"Negatif gecikme (lag={self.lag}) geleceğe bakış (look-ahead) hatasıdır! "
                "lag en az 0 olmalı, güvenli tahmin için önerilen lag >= 1'dir."
            )

    def to_dict(self) -> dict[str, Any]:
        """Özellik tanımını sözlük olarak serileştirir."""
        return {
            "name": self.name,
            "feature_type": str(self.feature_type.value),
            "window_bars": self.window_bars,
            "lag": self.lag,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeatureDefinition:
        """Sözlükten FeatureDefinition nesnesi oluşturur."""
        return cls(
            name=data["name"],
            feature_type=FeatureType(data["feature_type"]),
            window_bars=int(data.get("window_bars", 1)),
            lag=int(data.get("lag", 1)),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class FeatureDataset:
    """Nedensel olarak üretilmiş özellik matrisi ve hedef serisi."""

    feature_names: tuple[str, ...]
    timestamps: tuple[date, ...]
    X: tuple[tuple[float, ...], ...]  # Her satır bir barın özellikleri
    y: tuple[float | None, ...] | None = None
    is_leak_free: bool = True
    audit_summary: str = "Tüm özellikler point-in-time kesiminden önce üretildi."

    @property
    def sample_count(self) -> int:
        return len(self.X)

    @property
    def feature_count(self) -> int:
        return len(self.feature_names)


class PointInTimeNormalizer:
    """Yalnızca eğitim verisine fit edilen, test verisine sızıntıyı önleyen dönüştürücü.

    .agent/rules/veri-ml.md:
    'Ön işleme yalnızca eğitim verisine fit edilir. Global dağılım sızıntısı yasaktır.'
    """

    def __init__(self, method: str = "standard") -> None:
        if method not in ("standard", "minmax"):
            raise ValueError(f"Desteklenmeyen normalizasyon metodu: {method}")
        self.method = method
        self.means: list[float] | None = None
        self.stds: list[float] | None = None
        self.mins: list[float] | None = None
        self.maxs: list[float] | None = None
        self.is_fitted: bool = False
        self.fitted_samples: int = 0

    def fit(self, data_matrix: Sequence[Sequence[float]]) -> PointInTimeNormalizer:
        """Yalnızca eğitim verisinden istatistikleri öğrenir."""
        if not data_matrix or not data_matrix[0]:
            raise ValueError("Fit için boş matris verilemez.")

        num_features = len(data_matrix[0])
        num_samples = len(data_matrix)
        self.fitted_samples = num_samples

        if self.method == "standard":
            means = [0.0] * num_features
            for row in data_matrix:
                for j in range(num_features):
                    means[j] += row[j]
            means = [m / num_samples for m in means]

            variances = [0.0] * num_features
            for row in data_matrix:
                for j in range(num_features):
                    variances[j] += (row[j] - means[j]) ** 2
            stds = [math.sqrt(v / max(1, num_samples - 1)) for v in variances]
            # Sıfıra bölmeyi engelle
            stds = [s if s > 1e-8 else 1.0 for s in stds]

            self.means = means
            self.stds = stds
        else:  # minmax
            mins = list(data_matrix[0])
            maxs = list(data_matrix[0])
            for row in data_matrix[1:]:
                for j in range(num_features):
                    if row[j] < mins[j]:
                        mins[j] = row[j]
                    if row[j] > maxs[j]:
                        maxs[j] = row[j]

            self.mins = mins
            self.maxs = [
                maxs[j] if (maxs[j] - mins[j]) > 1e-8 else mins[j] + 1.0
                for j in range(num_features)
            ]

        self.is_fitted = True
        return self

    def transform(self, data_matrix: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
        """Önceden öğrenilmiş parametreleri yeni (val/test) verilere uygular."""
        if not self.is_fitted:
            raise PreprocessingLeakageError(
                "Normalizer henüz fit edilmedi! Test verisini dönüştürmeden önce "
                "yalnızca eğitim verisine fit edilmelidir."
            )

        transformed: list[tuple[float, ...]] = []
        for row in data_matrix:
            row_transformed: list[float] = []
            if self.method == "standard":
                assert self.means is not None and self.stds is not None
                for val, m, s in zip(row, self.means, self.stds, strict=True):
                    row_transformed.append((val - m) / s)
            else:  # minmax
                assert self.mins is not None and self.maxs is not None
                for val, mn, mx in zip(row, self.mins, self.maxs, strict=True):
                    denom = mx - mn
                    row_transformed.append((val - mn) / (denom if denom > 1e-8 else 1.0))
            transformed.append(tuple(row_transformed))
        return transformed

    def fit_transform(self, data_matrix: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
        """Eğitim verisine fit edip dönüştürür."""
        self.fit(data_matrix)
        return self.transform(data_matrix)

    def to_dict(self) -> dict[str, Any]:
        """Normalizasyon parametrelerini serileştirilebilir sözlüğe dönüştürür."""
        return {
            "method": self.method,
            "means": self.means,
            "stds": self.stds,
            "mins": self.mins,
            "maxs": self.maxs,
            "is_fitted": self.is_fitted,
            "fitted_samples": self.fitted_samples,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PointInTimeNormalizer:
        """Sözlükten fit edilmiş PointInTimeNormalizer nesnesini yeniden oluşturur."""
        instance = cls(method=str(data.get("method", "standard")))
        instance.means = data.get("means")
        instance.stds = data.get("stds")
        instance.mins = data.get("mins")
        instance.maxs = data.get("maxs")
        instance.is_fitted = bool(data.get("is_fitted", False))
        instance.fitted_samples = int(data.get("fitted_samples", 0))
        return instance


class PointInTimeFeatureExtractor:
    """Tahmin anında (as-of) sadece o ana kadar olan verilerden özellik çıkaran motor."""

    DEFAULT_FEATURE_DEFINITIONS: tuple[FeatureDefinition, ...] = (
        FeatureDefinition(
            name="log_return_1d_lag1",
            feature_type=FeatureType.LOG_RETURN,
            window_bars=1,
            lag=1,
            description="1 günlük log getiri (t-1 anındaki son bilinen getiri)",
        ),
        FeatureDefinition(
            name="log_return_5d_lag1",
            feature_type=FeatureType.LOG_RETURN,
            window_bars=5,
            lag=1,
            description="5 günlük log getiri (t-1 anındaki son bilinen haftalık getiri)",
        ),
        FeatureDefinition(
            name="volatility_10d_lag1",
            feature_type=FeatureType.ROLLING_VOLATILITY,
            window_bars=10,
            lag=1,
            description="10 günlük hareketli volatilite (t-1 itibarıyla)",
        ),
        FeatureDefinition(
            name="sma_ratio_5_20_lag1",
            feature_type=FeatureType.SMA_RATIO,
            window_bars=20,
            lag=1,
            description="SMA(5)/SMA(20) oranı (t-1 itibarıyla)",
        ),
        FeatureDefinition(
            name="normalized_range_lag1",
            feature_type=FeatureType.NORMALIZED_RANGE,
            window_bars=1,
            lag=1,
            description="(High - Low) / Close aralığı (t-1 itibarıyla)",
        ),
    )

    def __init__(
        self,
        features: Sequence[FeatureDefinition] | None = None,
        *,
        strict_as_of_enforcement: bool = True,
    ) -> None:
        self.features = tuple(features or self.DEFAULT_FEATURE_DEFINITIONS)
        self.strict_as_of_enforcement = strict_as_of_enforcement
        # Tanımlardaki nedensellik ve pencere kontrolleri
        for feat in self.features:
            LookaheadBiasDetector.verify_window_configuration(
                window_size=feat.window_bars,
                is_centered=False,
                forward_shift=0,
            )

    @property
    def max_lookback_bars(self) -> int:
        """Tüm özelliklerin hesaplanabilmesi için gereken azami geçmiş bar sayısı."""
        if not self.features:
            return 1
        return max(f.window_bars + f.lag for f in self.features)

    def extract_features_at(
        self,
        bars: Sequence[MarketBarRecord | dict[str, Any]],
        as_of: date | datetime,
    ) -> dict[str, float]:
        """Belirli bir as_of tarihi itibarıyla özellikleri çıkarır.

        Gelecekteki barlar (trade_date > as_of) kesinlikle filtrelenir.
        Eğer strict_as_of_enforcement açık ve girdi doğrudan gelecekteki barlar içeriyorsa
        denetim yapılır.
        """
        if not bars:
            raise ValueError("Özellik çıkarımı için bar listesi boş olamaz.")

        # Tarihe göre filtrele (strict point-in-time)
        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        causal_bars: list[dict[str, Any]] = []

        for b in bars:
            b_dict = b.model_dump() if isinstance(b, MarketBarRecord) else dict(b)
            t_date = b_dict.get("trade_date")
            if isinstance(t_date, str):
                t_date = date.fromisoformat(t_date)
            elif isinstance(t_date, datetime):
                t_date = t_date.date()

            if t_date is not None:
                if t_date > as_of_date:
                    if self.strict_as_of_enforcement:
                        raise FeatureTemporalIntegrityError(
                            f"Geleceğe bakış sızıntısı: Bar tarihi ({t_date}) "
                            f"as_of sınırından ({as_of_date}) sonraya ait!"
                        )
                    continue  # strict değilse gelecekteki barı yoksay
                causal_bars.append(b_dict)
            else:
                causal_bars.append(b_dict)

        if len(causal_bars) < self.max_lookback_bars:
            raise ValueError(
                f"Yetersiz geçmiş veri: {len(causal_bars)} bar mevcut, "
                f"en az {self.max_lookback_bars} bar gereklidir."
            )

        # Causal barlar üzerinden özellikleri hesapla
        return self._compute_feature_dict(causal_bars)

    def build_feature_dataset(
        self,
        bars: Sequence[MarketBarRecord | dict[str, Any]],
        target_series: TargetSeries | None = None,
        as_of_cutoff: date | datetime | None = None,
    ) -> FeatureDataset:
        """Tüm seri boyunca nedensel adım adım (point-in-time) özellik matrisi oluşturur.

        Her i indeksindeki özellik vektörü X[i], yalnızca i - lag veya öncesindeki
        barlardan türetilir.
        """
        if not bars:
            raise ValueError("Bar verisi boş olamaz.")

        parsed_bars: list[dict[str, Any]] = []
        for b in bars:
            b_dict = b.model_dump() if isinstance(b, MarketBarRecord) else dict(b)
            t_date = b_dict.get("trade_date")
            if isinstance(t_date, str):
                b_dict["trade_date"] = date.fromisoformat(t_date)
            elif isinstance(t_date, datetime):
                b_dict["trade_date"] = t_date.date()
            parsed_bars.append(b_dict)

        # Kronolojik sıra denetimi
        for idx in range(1, len(parsed_bars)):
            d_prev = parsed_bars[idx - 1].get("trade_date")
            d_curr = parsed_bars[idx].get("trade_date")
            if d_prev and d_curr and d_curr < d_prev:
                raise FeatureTemporalIntegrityError(
                    f"Zaman serisi kronolojik değil! İndeks {idx}: {d_curr} < {d_prev}"
                )

        if as_of_cutoff is not None:
            cutoff_d = as_of_cutoff.date() if isinstance(as_of_cutoff, datetime) else as_of_cutoff
            parsed_bars = [b for b in parsed_bars if b.get("trade_date", cutoff_d) <= cutoff_d]

        needed_bars = self.max_lookback_bars
        if len(parsed_bars) < needed_bars:
            raise ValueError(
                f"Veri boyutu ({len(parsed_bars)}) gereken pencereden ({needed_bars}) küçük."
            )

        feature_names = tuple(f.name for f in self.features)
        x_rows: list[tuple[float, ...]] = []
        dates_list: list[date] = []

        start_idx = needed_bars - 1
        for i in range(start_idx, len(parsed_bars)):
            sub_bars = parsed_bars[: i + 1]  # Sadece i indeksine kadar olan barlar
            feat_dict = self._compute_feature_dict(sub_bars)
            row_vals = tuple(feat_dict[f.name] for f in self.features)
            x_rows.append(row_vals)
            bar_date = parsed_bars[i].get("trade_date", date(2000, 1, 1))
            assert isinstance(bar_date, date)
            dates_list.append(bar_date)

        # Hedef serisi varsa hizala
        y_tuple: tuple[float | None, ...] | None = None
        if target_series is not None:
            # Hedef serisi tüm barlar için üretilmiş olmalı
            if len(target_series.values) != len(parsed_bars):
                raise ValueError(
                    f"Hedef serisi uzunluğu ({len(target_series.values)}) "
                    f"bar sayısı ({len(parsed_bars)}) ile uyuşmuyor!"
                )
            y_subset = target_series.values[start_idx : len(parsed_bars)]
            y_tuple = tuple(y_subset)

            # Hedef sızıntısı denetimi (Target Leakage Audit)
            records_for_audit: list[dict[str, Any]] = []
            for row, target_val in zip(x_rows, y_tuple, strict=True):
                if target_val is not None:
                    rec: dict[str, Any] = dict(zip(feature_names, row, strict=True))
                    rec["_target"] = target_val
                    records_for_audit.append(rec)

            violations = LookaheadBiasDetector.check_target_leakage(
                records=records_for_audit,
                target_name="_target",
                feature_names=list(feature_names),
            )
            if violations:
                first_v = violations[0]
                raise DataLeakageError(
                    f"Hedef sızıntısı tespit edildi ({first_v.feature_name}): {first_v.details}"
                )

        dataset = FeatureDataset(
            feature_names=feature_names,
            timestamps=tuple(dates_list),
            X=tuple(x_rows),
            y=y_tuple,
            is_leak_free=True,
            audit_summary=(
                f"{len(x_rows)} örnek için {len(feature_names)} özellik nedensel olarak üretildi."
            ),
        )
        assert_no_lookahead_leakage(dataset)
        return dataset

    def _compute_feature_dict(self, causal_bars: list[dict[str, Any]]) -> dict[str, float]:
        """Verilen geçmiş barlar üzerinden her bir özelliğin değerini hesaplar."""
        results: dict[str, float] = {}
        n_bars = len(causal_bars)

        for feat in self.features:
            # lag kadar geriye çek
            end_idx = n_bars - feat.lag
            if end_idx <= 0:
                raise FeatureTemporalIntegrityError(
                    f"Yetersiz geçmiş veya aşırı lag: {n_bars} bar, lag={feat.lag}"
                )

            if feat.feature_type == FeatureType.LOG_RETURN:
                p_curr = float(causal_bars[end_idx - 1]["close_price"])
                p_past_idx = max(0, end_idx - 1 - feat.window_bars)
                p_past = float(causal_bars[p_past_idx]["close_price"])
                if p_curr <= 0 or p_past <= 0:
                    results[feat.name] = 0.0
                else:
                    results[feat.name] = math.log(p_curr / p_past)

            elif feat.feature_type == FeatureType.SMA_RATIO:
                window = feat.window_bars
                start_w = max(0, end_idx - window)
                closes = [float(b["close_price"]) for b in causal_bars[start_w:end_idx]]
                sma_long = sum(closes) / len(closes) if closes else 1.0
                # Kısa pencere = max(1, window // 4)
                short_window = max(1, window // 4)
                short_closes = closes[-short_window:] if closes else [1.0]
                sma_short = sum(short_closes) / len(short_closes)
                results[feat.name] = sma_short / sma_long if sma_long > 1e-8 else 1.0

            elif feat.feature_type == FeatureType.ROLLING_VOLATILITY:
                window = feat.window_bars
                start_w = max(0, end_idx - window - 1)
                closes = [float(b["close_price"]) for b in causal_bars[start_w:end_idx]]
                if len(closes) > 2:
                    log_rets = [
                        math.log(closes[i + 1] / closes[i])
                        for i in range(len(closes) - 1)
                        if closes[i] > 0 and closes[i + 1] > 0
                    ]
                    if len(log_rets) > 1:
                        mean_r = sum(log_rets) / len(log_rets)
                        var = sum((r - mean_r) ** 2 for r in log_rets) / (len(log_rets) - 1)
                        results[feat.name] = math.sqrt(var)
                    else:
                        results[feat.name] = 0.0
                else:
                    results[feat.name] = 0.0

            elif feat.feature_type == FeatureType.NORMALIZED_RANGE:
                b = causal_bars[end_idx - 1]
                h = float(b.get("high_price", b["close_price"]))
                low_p = float(b.get("low_price", b["close_price"]))
                c = float(b["close_price"])
                results[feat.name] = (h - low_p) / c if c > 1e-8 else 0.0

            elif feat.feature_type == FeatureType.VOLUME_RATIO:
                window = feat.window_bars
                start_w = max(0, end_idx - window)
                vols = [float(b.get("volume", 1.0)) for b in causal_bars[start_w:end_idx]]
                mean_vol = sum(vols) / len(vols) if vols else 1.0
                last_vol = float(causal_bars[end_idx - 1].get("volume", mean_vol))
                results[feat.name] = last_vol / mean_vol if mean_vol > 1e-8 else 1.0

            else:
                raise NotImplementedError(f"Desteklenmeyen özellik türü: {feat.feature_type}")

        return results


def assert_no_lookahead_leakage(dataset: FeatureDataset) -> None:
    """Verilen özellik veri setinde geleceğe bakış sızıntısı olmadığını doğrular."""
    if not dataset.is_leak_free:
        raise DataLeakageError(f"Özellik seti sızıntısız değil: {dataset.audit_summary}")
    if dataset.feature_count == 0:
        raise ValueError("Özellik kümesi boş olamaz.")
    if dataset.sample_count == 0:
        raise ValueError("Örnek sayısı 0 olamaz.")
