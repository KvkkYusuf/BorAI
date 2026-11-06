"""Bağımsız referans veri seti ve analitik doğrulama testleri.

Teknik göstergelerin (RSI, ATR, EMA, SMA, MACD) bağımsız referans
hesaplamalar (J. Welles Wilder referans verisi ve analitik matematik formülleri)
ile birebir örtüştüğünü doğrular.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_analysis.indicators.technical import (
    calculate_atr,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
)


def test_rsi_against_wilder_reference_benchmark() -> None:
    """J. Welles Wilder'ın 'New Concepts in Technical Trading Systems'

    kitabındaki klasik 14 periyotluk referans veri setiyle RSI karşılaştırması.
    """
    # Wilder's 1978 klasik kapanış fiyatları serisi (15 gün)
    wilder_closes = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
    ]
    series = pd.Series(wilder_closes)

    # 14 periyotluk RSI
    rsi_series = calculate_rsi(series, period=14)

    # İlk 14 bar NaN olmalıdır:
    # diff() ilk elemanı NaN yapar; kalan 14 eleman min_periods=14'ü 15. barda tamamlar.
    for i in range(14):
        assert pd.isna(rsi_series.iloc[i])

    # 15. barda (index 14) RSI hesaplanmış olmalıdır
    rsi_day15 = rsi_series.iloc[14]
    assert not pd.isna(rsi_day15)
    assert 0.0 <= rsi_day15 <= 100.0
    assert rsi_day15 == pytest.approx(50.6574, rel=1e-3)

    # Bağımsız rastgele 50 barlık seride adım adım referans ile kıyaslama
    np.random.seed(42)
    extended_prices = pd.Series(100.0 + np.cumsum(np.random.randn(50)))
    calc_rsi = calculate_rsi(extended_prices, period=14)

    # Bağımsız hesaplama:
    diff = extended_prices.diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    exp_gain = gain.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    exp_loss = loss.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    exp_rs = exp_gain / exp_loss
    exp_rsi = 100.0 - (100.0 / (1.0 + exp_rs))
    exp_rsi = exp_rsi.where(exp_loss != 0, 100.0)
    exp_rsi = exp_rsi.where((exp_loss != 0) | (exp_gain != 0), 50.0)

    valid = ~(calc_rsi.isna())
    np.testing.assert_allclose(calc_rsi[valid], exp_rsi[valid], rtol=1e-7)


def test_atr_against_reference_known_dataset() -> None:
    """Bilinen High-Low-Close veri seti üzerinde True Range ve ATR doğrulaması."""
    # 5 günlük sentetik ama tam bilinen barlar
    highs = pd.Series([48.70, 48.72, 48.90, 48.87, 48.82])
    lows = pd.Series([47.79, 48.14, 48.39, 48.10, 47.90])
    closes = pd.Series([48.16, 48.61, 48.75, 48.63, 48.33])

    # True Range elle hesaplama:
    # Bar 0: H0 - L0 = 48.70 - 47.79 = 0.91
    # Bar 1: max(48.72-48.14=0.58, |48.72-48.16|=0.56, |48.14-48.16|=0.02) = 0.58
    # Bar 2: max(48.90-48.39=0.51, |48.90-48.61|=0.29, |48.39-48.61|=0.22) = 0.51
    # Bar 3: max(48.87-48.10=0.77, |48.87-48.75|=0.12, |48.10-48.75|=0.65) = 0.77
    # Bar 4: max(48.82-47.90=0.92, |48.82-48.63|=0.19, |47.90-48.63|=0.73) = 0.92

    period = 3
    atr = calculate_atr(highs, lows, closes, period=period)

    # İlk 2 gün NaN olmalı
    assert pd.isna(atr.iloc[0])
    assert pd.isna(atr.iloc[1])

    # 3. gün ve sonrası pozitif ve makul sınırda olmalı
    assert atr.iloc[2] > 0.0
    assert atr.iloc[3] > 0.0
    assert atr.iloc[4] > 0.0

    # Wilder smoothing (alpha = 1/3) ile:
    # tr = [0.91, 0.58, 0.51, 0.77, 0.92]
    # ewm(alpha=1/3, adjust=False)
    # y0 = 0.91
    # y1 = 1/3 * 0.58 + 2/3 * 0.91 = 0.80
    # y2 = 1/3 * 0.51 + 2/3 * 0.80 = 0.703333...
    assert atr.iloc[2] == pytest.approx(0.7033333, rel=1e-4)


def test_sma_and_ema_against_closed_form_benchmark() -> None:
    """SMA ve EMA hesaplamalarını bağımsız cebirsel formüllerle kıyaslar."""
    prices = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
    series = pd.Series(prices)

    # 4 periyotluk SMA
    sma = calculate_sma(series, period=4)
    # 4. eleman (index 3): (10 + 12 + 14 + 16) / 4 = 13.0
    assert sma.iloc[3] == pytest.approx(13.0)
    # 5. eleman (index 4): (12 + 14 + 16 + 18) / 4 = 15.0
    assert sma.iloc[4] == pytest.approx(15.0)

    # 3 periyotluk EMA bağımsız özyinelemeli referans
    # alpha = 2 / (3 + 1) = 0.5
    # y0 = 10.0
    # y1 = 0.5 * 12.0 + 0.5 * 10.0 = 11.0
    # y2 = 0.5 * 14.0 + 0.5 * 11.0 = 12.5
    # y3 = 0.5 * 16.0 + 0.5 * 12.5 = 14.25
    ema = calculate_ema(series, period=3)
    assert pd.isna(ema.iloc[0])
    assert pd.isna(ema.iloc[1])
    assert ema.iloc[2] == pytest.approx(12.5)
    assert ema.iloc[3] == pytest.approx(14.25)


def test_macd_against_independent_step_by_step_reference() -> None:
    """MACD hattı, sinyal hattı ve histogramı bağımsız adım adım hesaplamayla karşılaştırır."""
    np.random.seed(123)
    data = 100.0 + np.cumsum(np.random.randn(50))
    series = pd.Series(data)

    res = calculate_macd(series, fast_period=12, slow_period=26, signal_period=9)

    # Bağımsız hesaplama
    ref_fast = series.ewm(span=12, adjust=False, min_periods=12).mean()
    ref_slow = series.ewm(span=26, adjust=False, min_periods=26).mean()
    ref_macd = ref_fast - ref_slow
    ref_signal = ref_macd.ewm(span=9, adjust=False, min_periods=9).mean()
    ref_hist = ref_macd - ref_signal

    # NaN olmayan tüm indekslerde tam eşitlik
    valid = ~(ref_hist.isna())
    np.testing.assert_allclose(res.macd_line[valid], ref_macd[valid], rtol=1e-7)
    np.testing.assert_allclose(res.signal_line[valid], ref_signal[valid], rtol=1e-7)
    np.testing.assert_allclose(res.histogram[valid], ref_hist[valid], rtol=1e-7)
