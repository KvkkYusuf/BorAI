"""Tests for multi-symbol price scaling, normalization, and corporate action integration."""

import math
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_analysis.pipeline import (
    CorporateAction,
    CorporateActionType,
    MultiSymbolPriceScaler,
    ScalingMethod,
)
from stock_analysis.providers.base import MarketBarRecord


def _make_bar(
    symbol: str,
    trade_date: date,
    price: float,
    volume: int = 100_000,
) -> MarketBarRecord:
    """Helper to create test market bar with specified price and volume."""
    d_p = Decimal(str(round(price, 4)))
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        currency="TRY",
        raw_open=d_p,
        raw_high=Decimal(str(round(price * 1.02, 4))),
        raw_low=Decimal(str(round(price * 0.98, 4))),
        raw_close=d_p,
        adj_open=d_p,
        adj_high=Decimal(str(round(price * 1.02, 4))),
        adj_low=Decimal(str(round(price * 0.98, 4))),
        adj_close=d_p,
        volume=volume,
        turnover_try=Decimal(str(round(price * volume, 2))),
        source_name="test_data",
        is_verified=True,
    )


def test_multi_symbol_scale_discrepancy_resolved() -> None:
    """Test that low-priced and high-priced stocks are brought to comparable scales."""
    base_date = date(2026, 1, 1)
    bars_low: list[MarketBarRecord] = []
    bars_high: list[MarketBarRecord] = []

    for i in range(30):
        d = base_date + timedelta(days=i)
        # Low price stock: around 10 TRY (fluctuating between 9 and 11)
        p_low = 10.0 + (math.sin(i) * 1.0)
        bars_low.append(_make_bar("LOWP", d, p_low))

        # High price stock: around 1000 TRY (fluctuating between 950 and 1050)
        p_high = 1000.0 + (math.sin(i) * 50.0)
        bars_high.append(_make_bar("HIGHP", d, p_high))

    all_bars = bars_low + bars_high

    # Standard (z-score) scaling
    scaler = MultiSymbolPriceScaler(method=ScalingMethod.STANDARD)
    scaled_features = scaler.fit_transform(all_bars)

    assert len(scaled_features) == 60
    assert scaler.is_fitted
    assert scaler.fitted_symbols == {"LOWP", "HIGHP"}

    low_closes = [f.close_scaled for f in scaled_features if f.symbol == "LOWP"]
    high_closes = [f.close_scaled for f in scaled_features if f.symbol == "HIGHP"]

    # Both symbols should have mean near 0 and std near 1
    low_mean = sum(low_closes) / len(low_closes)
    high_mean = sum(high_closes) / len(high_closes)
    assert abs(low_mean) < 0.15
    assert abs(high_mean) < 0.15

    # Range of features should be comparable (-3 to +3), not 10 vs 1000
    assert max(low_closes) - min(low_closes) < 5.0
    assert max(high_closes) - min(high_closes) < 5.0


def test_minmax_scaling_range() -> None:
    """Verify MinMax scaling strictly places values within specified feature_range."""
    base_date = date(2026, 1, 1)
    bars = [_make_bar("THYAO", base_date + timedelta(days=i), 250.0 + i * 2.0) for i in range(20)]

    scaler = MultiSymbolPriceScaler(
        method=ScalingMethod.MINMAX,
        feature_range=(-1.0, 1.0),
    )
    features = scaler.fit_transform(bars)

    for f in features:
        assert -1.0001 <= f.close_scaled <= 1.0001


def test_corporate_action_split_and_volume_adjustment() -> None:
    """Test that corporate action split eliminates artificial price cliff and adjusts volume."""
    # 2:1 split on 2026-01-15:
    # Before 2026-01-15: raw price was 200, volume was 50,000
    # From 2026-01-15: raw price is 100, volume is 100,000
    bars: list[MarketBarRecord] = []
    base_date = date(2026, 1, 1)

    for i in range(20):
        d = base_date + timedelta(days=i)
        if d < date(2026, 1, 15):
            bars.append(_make_bar("SPLT", d, price=200.0, volume=50_000))
        else:
            bars.append(_make_bar("SPLT", d, price=100.0, volume=100_000))

    split_action = CorporateAction(
        symbol="SPLT",
        ex_date=date(2026, 1, 15),
        action_type=CorporateActionType.SPLIT,
        ratio=Decimal("2.0"),
        description="2:1 forward split",
    )

    scaler = MultiSymbolPriceScaler(
        method=ScalingMethod.STANDARD,
        adjust_volume_for_splits=True,
    )
    features = scaler.fit_transform(bars, actions=[split_action])

    # Check adjusted close prices: pre-split 200 should be adjusted to 100
    for f in features:
        assert f.adj_close == 100.0
        # Pre-split dates should have adjustment_factor = 0.5
        if f.trade_date < date(2026, 1, 15):
            assert f.adjustment_factor == 0.5

    # Check that prices are now constant (100.0), so scaled values are all identical (near 0)
    scaled_closes = [f.close_scaled for f in features]
    assert max(scaled_closes) - min(scaled_closes) < 1e-4

    # Check volume adjustment: 50,000 pre-split with 2.0 ratio became 100,000
    vol_scaled = [f.volume_scaled for f in features]
    assert max(vol_scaled) - min(vol_scaled) < 1e-4


def test_leakage_prevention_train_only_fit() -> None:
    """Verify scaler parameters are learned ONLY from train split and apply to test."""
    train_bars = [_make_bar("BIST1", date(2026, 1, i + 1), 100.0 + i) for i in range(10)]
    test_bars = [_make_bar("BIST1", date(2026, 1, i + 11), 110.0 + i) for i in range(10)]

    scaler = MultiSymbolPriceScaler(method=ScalingMethod.STANDARD)
    scaler.fit(train_bars)

    train_params = scaler.get_params("BIST1")
    expected_mean = sum(100.0 + i for i in range(10)) / 10.0
    assert abs(train_params.center - expected_mean) < 1e-4
    assert train_params.fit_sample_count == 10
    assert train_params.fit_start_date == date(2026, 1, 1)
    assert train_params.fit_end_date == date(2026, 1, 10)

    # Transform test bars: must use train center and scale, not test mean
    test_features = scaler.transform(test_bars)
    assert len(test_features) == 10
    # Since test prices are > 110 (above train mean 104.5), close_scaled must be positive
    for tf in test_features:
        assert tf.close_scaled > 1.0


def test_unseen_symbol_in_transform_raises_error() -> None:
    """Test that attempting to transform an unseen symbol raises ValueError."""
    train_bars = [_make_bar("SYM1", date(2026, 1, 1), 50.0)]
    scaler = MultiSymbolPriceScaler().fit(train_bars)

    test_bars = [_make_bar("UNSEEN", date(2026, 1, 2), 60.0)]
    with pytest.raises(ValueError, match="Symbol 'UNSEEN' was not present during fit"):
        scaler.transform(test_bars)


def test_transform_before_fit_raises_error() -> None:
    """Test that calling transform without fitting raises RuntimeError."""
    scaler = MultiSymbolPriceScaler()
    with pytest.raises(RuntimeError, match="must be fit before calling transform"):
        scaler.transform([_make_bar("SYM1", date(2026, 1, 1), 50.0)])


def test_inverse_transform_reconstruction() -> None:
    """Verify inverse_transform_close accurately maps back to original nominal prices."""
    bars = [_make_bar("AKBNK", date(2026, 2, i + 1), 60.0 + (i * 1.5)) for i in range(15)]
    original_prices = [float(b.raw_close) for b in bars]

    for method in (ScalingMethod.STANDARD, ScalingMethod.MINMAX, ScalingMethod.ROBUST):
        scaler = MultiSymbolPriceScaler(method=method)
        features = scaler.fit_transform(bars)
        scaled_closes = [f.close_scaled for f in features]

        reconstructed = scaler.inverse_transform_close("AKBNK", scaled_closes)
        for orig, recon in zip(original_prices, reconstructed, strict=True):
            assert abs(orig - recon) < 1e-3, f"Mismatch in method {method}: {orig} vs {recon}"


def test_export_and_import_params() -> None:
    """Test full serialization and checkpointing reproducibility."""
    bars = [_make_bar("EREGL", date(2026, 3, i + 1), 45.0 + i) for i in range(10)]
    scaler = MultiSymbolPriceScaler(method=ScalingMethod.ROBUST)
    features_orig = scaler.fit_transform(bars)

    # Export params and rebuild scaler
    exported = scaler.export_params()
    reloaded_scaler = MultiSymbolPriceScaler.from_params(exported)

    assert reloaded_scaler.is_fitted
    assert reloaded_scaler.fitted_symbols == {"EREGL"}

    features_reloaded = reloaded_scaler.transform(bars)
    for f1, f2 in zip(features_orig, features_reloaded, strict=True):
        assert f1.close_scaled == f2.close_scaled
        assert f1.volume_scaled == f2.volume_scaled


def test_constant_price_zero_variance_safety() -> None:
    """Test that zero-variance stock does not cause division by zero."""
    bars = [_make_bar("FLAT", date(2026, 1, i + 1), 10.0) for i in range(5)]
    scaler = MultiSymbolPriceScaler(method=ScalingMethod.STANDARD)
    features = scaler.fit_transform(bars)

    assert len(features) == 5
    for f in features:
        assert not math.isnan(f.close_scaled)
        assert not math.isinf(f.close_scaled)
