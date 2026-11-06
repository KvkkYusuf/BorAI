"""Temel analitik ve teknik gösterge hesaplamaları.

Bu modül, harici yapay zekâ modellerine ihtiyaç duymaksızın
saf analitik ve matematiksel yöntemlerle finansal göstergeleri hesaplar.
Tüm hesaplamalar açıklanabilir, deterministik ve tekrar üretilebilirdir.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MACDResult:
    """MACD hesaplama çıktısı."""

    macd_line: pd.Series
    signal_line: pd.Series
    histogram: pd.Series


@dataclass(frozen=True)
class IndicatorSummary:
    """Belirli bir zaman dilimi veya son bar için özet gösterge değerleri."""

    symbol: str
    last_close: float
    sma_20: float | None
    sma_50: float | None
    ema_20: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    atr_14: float | None
    volume_sma_20: float | None
    volatility_20: float | None


def calculate_sma(series: pd.Series, period: int = 20) -> pd.Series:
    """Basit Hareketli Ortalama (Simple Moving Average - SMA).

    Formül: SMA = (P1 + P2 + ... + Pn) / n
    """
    if period <= 0:
        raise ValueError("Periyot pozitif bir tamsayı olmalıdır.")
    return series.rolling(window=period, min_periods=period).mean()


def calculate_ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Üstel Hareketli Ortalama (Exponential Moving Average - EMA).

    Formül: EMA_t = alpha * P_t + (1 - alpha) * EMA_{t-1}
    Burada alpha = 2 / (period + 1)
    """
    if period <= 0:
        raise ValueError("Periyot pozitif bir tamsayı olmalıdır.")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Göreceli Güç Endeksi (Relative Strength Index - RSI).

    Wilder's RSI:
    RS = Ortalama Kazanç / Ortalama Kayıp
    RSI = 100 - (100 / (1 + RS))
    """
    if period <= 0:
        raise ValueError("Periyot pozitif bir tamsayı olmalıdır.")

    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder smoothing (alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Kayıp sıfırsa RSI 100, hem kazanç hem kayıp sıfırsa RSI 50
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where((avg_loss != 0) | (avg_gain != 0), 50.0)

    return rsi


def calculate_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MACDResult:
    """Hareketli Ortalama Yakınsama/Iraksama (MACD).

    MACD Line = EMA(fast) - EMA(slow)
    Signal Line = EMA(MACD Line, signal_period)
    Histogram = MACD Line - Signal Line
    """
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("Tüm periyotlar pozitif tamsayı olmalıdır.")
    if fast_period >= slow_period:
        raise ValueError(
            "Hızlı periyot (fast_period) yavaş periyottan (slow_period) küçük olmalıdır."
        )

    fast_ema = calculate_ema(series, period=fast_period)
    slow_ema = calculate_ema(series, period=slow_period)

    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = macd_line - signal_line

    return MACDResult(
        macd_line=macd_line,
        signal_line=signal_line,
        histogram=histogram,
    )


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Ortalama Gerçek Aralık (Average True Range - ATR).

    True Range (TR) = max(H - L, |H - C_prev|, |L - C_prev|)
    ATR = Wilder's EMA(TR, period)
    """
    if period <= 0:
        raise ValueError("Periyot pozitif bir tamsayı olmalıdır.")

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def calculate_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """İşlem Hacmi Basit Hareketli Ortalaması (Volume SMA)."""
    return calculate_sma(volume, period=period)


def calculate_volatility(
    close: pd.Series,
    period: int = 20,
    annualize: bool = False,
    trading_days: int = 252,
) -> pd.Series:
    """Tarihsel Oynaklık (Historical Volatility).

    Logaritmik getirilerin standart sapması.
    annualize=True ise yıllıklandırılmış oynaklık (sigma * sqrt(trading_days)) döndürür.
    """
    if period <= 1:
        raise ValueError("Oynaklık için periyot en az 2 olmalıdır.")

    ratio = close / close.shift(1)
    log_returns = pd.Series(np.log(ratio.to_numpy()), index=close.index, dtype=float)
    rolling_std = log_returns.rolling(window=period, min_periods=period).std()

    if annualize:
        rolling_std = rolling_std * float(np.sqrt(trading_days))

    return rolling_std


def calculate_all_indicators(
    df: pd.DataFrame,
    symbol: str = "UNKNOWN",
    close_col: str = "close",
    high_col: str = "high",
    low_col: str = "low",
    volume_col: str = "volume",
) -> pd.DataFrame:
    """Verilen OHLCV DataFrame'ine tüm temel gösterge sütunlarını ekler.

    Dönen DataFrame orijinal veriyi bozmaz, yeni kopyayı döndürür.
    """
    out = df.copy()
    close = out[close_col]

    out["sma_20"] = calculate_sma(close, 20)
    out["sma_50"] = calculate_sma(close, 50)
    out["ema_20"] = calculate_ema(close, 20)
    out["rsi_14"] = calculate_rsi(close, 14)

    macd_res = calculate_macd(close, 12, 26, 9)
    out["macd_line"] = macd_res.macd_line
    out["macd_signal"] = macd_res.signal_line
    out["macd_hist"] = macd_res.histogram

    if high_col in out.columns and low_col in out.columns:
        out["atr_14"] = calculate_atr(out[high_col], out[low_col], close, 14)

    if volume_col in out.columns:
        out["volume_sma_20"] = calculate_volume_sma(out[volume_col], 20)

    out["volatility_20"] = calculate_volatility(close, 20, annualize=False)

    return out


def get_latest_indicator_summary(
    df: pd.DataFrame,
    symbol: str,
    close_col: str = "close",
) -> IndicatorSummary:
    """En son bar için göstergelerin özet değerlerini döndürür."""
    enriched = calculate_all_indicators(df, symbol=symbol, close_col=close_col)
    if enriched.empty:
        raise ValueError("Veri seti boş.")

    last_row = enriched.iloc[-1]

    def _safe_float(val: object) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)  # type: ignore[arg-type]
            if np.isnan(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    return IndicatorSummary(
        symbol=symbol,
        last_close=float(last_row[close_col]),
        sma_20=_safe_float(last_row.get("sma_20")),
        sma_50=_safe_float(last_row.get("sma_50")),
        ema_20=_safe_float(last_row.get("ema_20")),
        rsi_14=_safe_float(last_row.get("rsi_14")),
        macd=_safe_float(last_row.get("macd_line")),
        macd_signal=_safe_float(last_row.get("macd_signal")),
        macd_hist=_safe_float(last_row.get("macd_hist")),
        atr_14=_safe_float(last_row.get("atr_14")),
        volume_sma_20=_safe_float(last_row.get("volume_sma_20")),
        volatility_20=_safe_float(last_row.get("volatility_20")),
    )
