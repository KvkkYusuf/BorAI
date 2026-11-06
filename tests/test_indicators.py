"""Birim testler: Temel teknik göstergeler ve analitik hesaplamalar."""

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.technical import (
    calculate_all_indicators,
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
    calculate_volatility,
    calculate_volume_sma,
    get_latest_indicator_summary,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Örnek 60 günlük OHLCV verisi."""
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    np.random.seed(42)
    # Basit bir rastgele yürüyüş
    returns = np.random.normal(0.001, 0.02, size=60)
    prices = 100.0 * np.cumprod(1.0 + returns)
    highs = prices * (1.0 + np.abs(np.random.normal(0.005, 0.005, size=60)))
    lows = prices * (1.0 - np.abs(np.random.normal(0.005, 0.005, size=60)))
    volumes = np.random.randint(10000, 500000, size=60)

    return pd.DataFrame(
        {
            "close": prices,
            "high": highs,
            "low": lows,
            "volume": volumes,
        },
        index=dates,
    )


def test_calculate_sma() -> None:
    series = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    sma3 = calculate_sma(series, period=3)

    assert pd.isna(sma3.iloc[0])
    assert pd.isna(sma3.iloc[1])
    assert sma3.iloc[2] == pytest.approx(20.0)
    assert sma3.iloc[3] == pytest.approx(30.0)
    assert sma3.iloc[4] == pytest.approx(40.0)

    with pytest.raises(ValueError, match="pozitif bir tamsayı"):
        calculate_sma(series, period=0)


def test_calculate_ema() -> None:
    series = pd.Series([10.0, 10.0, 10.0, 20.0, 20.0])
    ema = calculate_ema(series, period=3)
    assert pd.isna(ema.iloc[0])
    assert pd.isna(ema.iloc[1])
    assert ema.iloc[2] == pytest.approx(10.0)
    # alpha = 2 / (3 + 1) = 0.5
    # ema[3] = 0.5 * 20 + 0.5 * 10 = 15.0
    assert ema.iloc[3] == pytest.approx(15.0)


def test_calculate_rsi() -> None:
    # Sürekli artan veri -> RSI 100 olmalı
    uptrend = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0])
    rsi_up = calculate_rsi(uptrend, period=5)
    valid_up = rsi_up.dropna()
    assert len(valid_up) > 0
    assert (valid_up == 100.0).all()

    # Sürekli azalan veri -> RSI 0 olmalı
    downtrend = pd.Series([20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0])
    rsi_down = calculate_rsi(downtrend, period=5)
    valid_down = rsi_down.dropna()
    assert len(valid_down) > 0
    assert (valid_down == 0.0).all()

    # Sabit veri -> RSI 50 olmalı
    flat = pd.Series([10.0] * 10)
    rsi_flat = calculate_rsi(flat, period=5)
    assert rsi_flat.dropna().iloc[-1] == pytest.approx(50.0)


def test_calculate_macd() -> None:
    series = pd.Series([10.0 + i * 0.5 for i in range(40)])
    macd = calculate_macd(series, fast_period=12, slow_period=26, signal_period=9)

    assert len(macd.macd_line) == 40
    assert len(macd.signal_line) == 40
    assert len(macd.histogram) == 40

    # Tutarlılık: histogram = macd_line - signal_line
    valid_idx = ~(macd.histogram.isna())
    diff = macd.histogram[valid_idx] - (macd.macd_line[valid_idx] - macd.signal_line[valid_idx])
    assert np.allclose(diff, 0.0)

    # Geçersiz periyotlar kontrolü
    with pytest.raises(ValueError, match="Hızlı periyot"):
        calculate_macd(series, fast_period=26, slow_period=12)


def test_calculate_atr() -> None:
    # 5 günlük basit veri seti
    high = pd.Series([12.0, 13.0, 15.0, 14.0, 16.0])
    low = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0])
    close = pd.Series([11.0, 12.0, 13.0, 12.0, 15.0])

    atr = calculate_atr(high, low, close, period=3)
    assert len(atr) == 5
    assert not pd.isna(atr.iloc[-1])
    assert atr.iloc[-1] > 0.0


def test_calculate_volatility() -> None:
    series = pd.Series([100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 105.0])
    vol = calculate_volatility(series, period=5, annualize=False)
    assert pd.isna(vol.iloc[3])
    assert not pd.isna(vol.iloc[5])
    assert vol.iloc[5] > 0.0

    vol_annual = calculate_volatility(series, period=5, annualize=True, trading_days=252)
    assert vol_annual.iloc[5] == pytest.approx(vol.iloc[5] * np.sqrt(252))


def test_calculate_volume_sma() -> None:
    volumes = pd.Series([1000.0, 2000.0, 3000.0, 4000.0])
    vol_sma = calculate_volume_sma(volumes, period=2)
    assert vol_sma.iloc[1] == pytest.approx(1500.0)
    assert vol_sma.iloc[3] == pytest.approx(3500.0)


def test_calculate_all_indicators_and_summary(sample_ohlcv: pd.DataFrame) -> None:
    enriched = calculate_all_indicators(sample_ohlcv, symbol="THYAO")

    expected_cols = [
        "sma_20",
        "sma_50",
        "ema_20",
        "rsi_14",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "atr_14",
        "volume_sma_20",
        "volatility_20",
    ]
    for col in expected_cols:
        assert col in enriched.columns

    summary = get_latest_indicator_summary(sample_ohlcv, symbol="THYAO")
    assert summary.symbol == "THYAO"
    assert summary.last_close > 0.0
    assert summary.sma_20 is not None
    assert summary.rsi_14 is not None
    assert 0.0 <= summary.rsi_14 <= 100.0
    assert summary.atr_14 is not None
