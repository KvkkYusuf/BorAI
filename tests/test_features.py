"""Unit tests for point-in-time feature extraction and anti-leakage guards (FAZ-11)."""

from datetime import date, timedelta
from typing import Any

import pytest

from stock_analysis.models.features import (
    FeatureDataset,
    FeatureDefinition,
    FeatureTemporalIntegrityError,
    FeatureType,
    PointInTimeFeatureExtractor,
    PointInTimeNormalizer,
    PreprocessingLeakageError,
    assert_no_lookahead_leakage,
)
from stock_analysis.models.targets import (
    TargetConfig,
    TargetGenerator,
)
from stock_analysis.models.targets import (
    TargetType as MLTargetType,
)
from stock_analysis.pipeline.lookahead_bias import DataLeakageError


def _generate_synthetic_bars(
    count: int = 50, start_date: date = date(2026, 1, 1)
) -> list[dict[str, Any]]:
    """Testler için sentetik bar listesi oluşturur."""
    bars: list[dict[str, Any]] = []
    base_price = 100.0
    for i in range(count):
        d = start_date + timedelta(days=i)
        # Basit artan/dalgalanan seri
        close = base_price + (i * 0.5) + ((i % 3) - 1) * 0.2
        high = close + 1.0
        low = close - 1.0
        vol = 1000.0 + (i * 10.0)
        bars.append(
            {
                "symbol": "THYAO",
                "trade_date": d,
                "close_price": close,
                "high_price": high,
                "low_price": low,
                "volume": vol,
            }
        )
    return bars


def test_feature_definition_validation() -> None:
    """FeatureDefinition doğrulama ve negatif lag (lookahead) koruması."""
    # Geçerli
    f = FeatureDefinition(
        name="log_ret_1d",
        feature_type=FeatureType.LOG_RETURN,
        window_bars=1,
        lag=1,
    )
    assert f.lag == 1
    assert f.window_bars == 1

    # window_bars < 1 hatası
    with pytest.raises(ValueError, match="window_bars en az 1"):
        FeatureDefinition(
            name="err",
            feature_type=FeatureType.LOG_RETURN,
            window_bars=0,
            lag=1,
        )

    # Negatif lag (geleceğe bakış) hatası
    with pytest.raises(FeatureTemporalIntegrityError, match="Negatif gecikme"):
        FeatureDefinition(
            name="future_peek",
            feature_type=FeatureType.LOG_RETURN,
            window_bars=1,
            lag=-1,
        )


def test_point_in_time_normalizer_isolated() -> None:
    """Normalizer yalnızca eğitim verisinden öğrenmeli, test verisine dağılım sızdırmamalıdır."""
    normalizer = PointInTimeNormalizer(method="standard")

    # Fit edilmeden transform çağrılırsa hata vermelidir
    with pytest.raises(PreprocessingLeakageError, match="henüz fit edilmedi"):
        normalizer.transform([[1.0, 2.0]])

    train_data = [[10.0, 100.0], [20.0, 200.0], [30.0, 300.0]]
    normalizer.fit(train_data)
    assert normalizer.is_fitted
    assert normalizer.means == [20.0, 200.0]

    # Test verisi dönüştürülürken eğitim istatistikleri korunmalıdır
    test_data = [[20.0, 200.0], [40.0, 400.0]]
    transformed_test = normalizer.transform(test_data)

    # 20.0 ve 200.0 tam ortalama olduğu için 0.0 olmalıdır
    assert abs(transformed_test[0][0]) < 1e-6
    assert abs(transformed_test[0][1]) < 1e-6

    # 40.0 ortalamanın 2 birim standard sapma üzerindedir
    assert transformed_test[1][0] > 1.0


def test_point_in_time_normalizer_minmax() -> None:
    """Minmax normalizasyonunun doğruluğu."""
    normalizer = PointInTimeNormalizer(method="minmax")
    train_data = [[0.0, 50.0], [10.0, 100.0]]
    res = normalizer.fit_transform(train_data)
    assert res[0] == (0.0, 0.0)
    assert res[1] == (1.0, 1.0)


def test_point_in_time_extractor_as_of_cutoff() -> None:
    """as_of tarihinden sonraki verilerin sızması engellenmelidir."""
    bars = _generate_synthetic_bars(count=30, start_date=date(2026, 1, 1))
    extractor = PointInTimeFeatureExtractor(strict_as_of_enforcement=True)

    as_of = date(2026, 1, 25)
    # Gelecekteki bar içeren liste strict modda hata fırlatmalıdır
    with pytest.raises(FeatureTemporalIntegrityError, match="Geleceğe bakış sızıntısı"):
        extractor.extract_features_at(bars, as_of=as_of)

    # Yalnızca as_of tarihine kadar olan veriler verildiğinde temiz hesaplamalıdır
    valid_bars = [b for b in bars if b["trade_date"] <= as_of]
    feats = extractor.extract_features_at(valid_bars, as_of=as_of)
    assert "log_return_1d_lag1" in feats
    assert "volatility_10d_lag1" in feats
    assert "sma_ratio_5_20_lag1" in feats


def test_build_feature_dataset_temporal_causality() -> None:
    """Tüm özellik matrisi boyunca zamansal nedensellik korunmalıdır."""
    bars = _generate_synthetic_bars(count=40, start_date=date(2026, 1, 1))
    extractor = PointInTimeFeatureExtractor()

    dataset = extractor.build_feature_dataset(bars)
    assert dataset.is_leak_free
    assert dataset.sample_count > 0
    assert dataset.feature_count == len(extractor.features)

    # İndekslerin ve zaman damgalarının artan sırada olduğunu doğrula
    for i in range(1, len(dataset.timestamps)):
        assert dataset.timestamps[i] > dataset.timestamps[i - 1]


def test_build_feature_dataset_with_target() -> None:
    """Özellik veri seti hedef serisi ile hizalanmalı ve hedef sızıntısı olmamalıdır."""
    bars = _generate_synthetic_bars(count=40, start_date=date(2026, 1, 1))
    closes = [float(b["close_price"]) for b in bars]

    target_gen = TargetGenerator(TargetConfig(target_type=MLTargetType.LOG_RETURN, horizon_steps=1))
    target_series = target_gen.compute_target(closes)

    extractor = PointInTimeFeatureExtractor()
    dataset = extractor.build_feature_dataset(bars, target_series=target_series)

    assert dataset.y is not None
    assert len(dataset.y) == dataset.sample_count
    # Son adımda (horizon=1) hedef None olmalıdır
    assert dataset.y[-1] is None
    # Önceki adımlarda hedef geçerli float olmalıdır
    assert dataset.y[0] is not None


def test_non_chronological_bars_rejected() -> None:
    """Kronolojik olmayan veya ters sıralı barlar anında reddedilmelidir."""
    bars = _generate_synthetic_bars(count=30, start_date=date(2026, 1, 1))
    # Barların sırasını boz
    bars[5], bars[6] = bars[6], bars[5]

    extractor = PointInTimeFeatureExtractor()
    with pytest.raises(FeatureTemporalIntegrityError, match="Zaman serisi kronolojik değil"):
        extractor.build_feature_dataset(bars)


def test_assert_no_lookahead_leakage_empty_fails() -> None:
    """Boş veya sızıntılı veri setleri doğrulamadan geçememelidir."""
    empty_ds = FeatureDataset(
        feature_names=(),
        timestamps=(),
        X=(),
        is_leak_free=True,
    )
    with pytest.raises(ValueError, match="Özellik kümesi boş olamaz"):
        assert_no_lookahead_leakage(empty_ds)

    leaky_ds = FeatureDataset(
        feature_names=("feat",),
        timestamps=(date(2026, 1, 1),),
        X=((1.0,),),
        is_leak_free=False,
    )
    with pytest.raises(DataLeakageError, match="Özellik seti sızıntısız değil"):
        assert_no_lookahead_leakage(leaky_ds)
