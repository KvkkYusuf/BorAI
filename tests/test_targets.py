"""Tahmin hedefleri ve etiket üretim testleri."""

import math

import pytest

from stock_analysis.models.targets import (
    TargetConfig,
    TargetGenerator,
    TargetType,
    validate_single_target_constraint,
)


def test_target_config_validation() -> None:
    """TargetConfig doğrulama sınırlarını kontrol eder."""
    config = TargetConfig(target_type=TargetType.LOG_RETURN, horizon_steps=5)
    assert config.horizon_steps == 5
    assert config.target_type == TargetType.LOG_RETURN

    # Geçersiz ufuk
    with pytest.raises(ValueError, match="horizon_steps en az 1"):
        TargetConfig(target_type=TargetType.LOG_RETURN, horizon_steps=0)

    # Geçersiz eşik
    with pytest.raises(ValueError, match="direction_threshold negatif olamaz"):
        TargetConfig(target_type=TargetType.DIRECTION_CLASSIFICATION, direction_threshold=-0.01)

    # Geçersiz yıllıklandırma çarpanı
    with pytest.raises(ValueError, match="annualization_factor pozitif olmalıdır"):
        TargetConfig(target_type=TargetType.REALIZED_VOLATILITY, annualization_factor=0)


def test_single_target_constraint() -> None:
    """FAZ-11 kuralı: Tek modelde çoklu hedef karıştırmayı engelleme testi."""
    t1 = TargetConfig(target_type=TargetType.LOG_RETURN, horizon_steps=1)
    t2 = TargetConfig(target_type=TargetType.DIRECTION_CLASSIFICATION, horizon_steps=1)

    # Tek hedef geçerli
    assert validate_single_target_constraint([t1]) == t1

    # Çoklu hedef reddedilir
    with pytest.raises(ValueError, match="Aynı anda yalnızca 1 hedef yapılandırılabilir"):
        validate_single_target_constraint([t1, t2])

    # Sıfır hedef reddedilir
    with pytest.raises(ValueError, match="Aynı anda yalnızca 1 hedef yapılandırılabilir"):
        validate_single_target_constraint([])


def test_log_return_target_computation() -> None:
    """Log getiri hedefi doğru hesaplanmalı ve son H adım None olmalıdır."""
    prices = [100.0, 105.0, 110.0, 100.0]
    config = TargetConfig(target_type=TargetType.LOG_RETURN, horizon_steps=1)
    generator = TargetGenerator(config)
    res = generator.compute_target(prices)

    assert len(res.values) == 4
    assert res.valid_sample_count == 3
    assert res.values[3] is None
    assert math.isclose(res.values[0] or 0.0, math.log(1.05), rel_tol=1e-5)
    assert math.isclose(res.values[1] or 0.0, math.log(110.0 / 105.0), rel_tol=1e-5)
    assert math.isclose(res.values[2] or 0.0, math.log(100.0 / 110.0), rel_tol=1e-5)


def test_percent_return_target_computation() -> None:
    """Yüzde getiri hedefi doğru hesaplanmalıdır."""
    prices = [100.0, 102.0, 104.0, 108.0]
    config = TargetConfig(target_type=TargetType.PERCENT_RETURN, horizon_steps=2)
    generator = TargetGenerator(config)
    res = generator.compute_target(prices)

    assert len(res.values) == 4
    assert res.valid_sample_count == 2
    assert math.isclose(res.values[0] or 0.0, 0.04, rel_tol=1e-5)
    assert math.isclose(res.values[1] or 0.0, 6.0 / 102.0, rel_tol=1e-5)
    assert res.values[2] is None
    assert res.values[3] is None


def test_direction_classification_target() -> None:
    """Yön sınıflandırması eşiğe göre -1, 0, +1 üretmelidir."""
    prices = [100.0, 105.0, 105.1, 99.0, 100.0]
    config = TargetConfig(
        target_type=TargetType.DIRECTION_CLASSIFICATION,
        horizon_steps=1,
        direction_threshold=0.01,
    )
    generator = TargetGenerator(config)
    res = generator.compute_target(prices)

    assert res.values[0] == 1.0
    assert res.values[1] == 0.0
    assert res.values[2] == -1.0
    assert res.values[3] == 1.0
    assert res.values[4] is None


def test_realized_volatility_target() -> None:
    """Gerçekleşen volatilite standart sapma hesabını doğrular."""
    prices = [100.0, 102.0, 99.0, 103.0, 101.0, 105.0]
    config = TargetConfig(
        target_type=TargetType.REALIZED_VOLATILITY,
        horizon_steps=3,
        annualize_volatility=True,
        annualization_factor=252.0,
    )
    generator = TargetGenerator(config)
    res = generator.compute_target(prices)

    assert len(res.values) == len(prices)
    assert res.valid_sample_count == 3
    assert res.values[-1] is None
    assert res.values[-2] is None
    assert res.values[-3] is None

    # Değerlerin pozitif olduğunu doğrula
    for v in res.values[:3]:
        assert v is not None and v > 0.0


def test_target_generator_invalid_prices() -> None:
    """Sıfır, negatif veya yetersiz uzunlukta seri hatalarını yakalar."""
    config = TargetConfig(target_type=TargetType.LOG_RETURN, horizon_steps=3)
    generator = TargetGenerator(config)

    with pytest.raises(ValueError, match="tahmin ufkundan .* büyük olmalıdır"):
        generator.compute_target([100.0, 101.0])

    with pytest.raises(ValueError, match="Fiyatlar pozitif ve geçerli reel sayılar olmalıdır"):
        generator.compute_target([100.0, -10.0, 102.0, 103.0])
