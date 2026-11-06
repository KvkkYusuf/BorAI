"""Tests for the synthetic BIST EOD market data generator."""

from datetime import date
from decimal import Decimal

import pytest

from stock_analysis.synthetic import (
    BIST_SAMPLE_SYMBOLS,
    SyntheticDailyBar,
    generate_synthetic_dataframe,
    generate_synthetic_ohlcv,
)


def test_bist_sample_symbols() -> None:
    """Verify BIST sample symbols list is not empty and symbols are valid uppercase."""
    assert len(BIST_SAMPLE_SYMBOLS) > 0
    for symbol in BIST_SAMPLE_SYMBOLS:
        assert symbol.isupper()
        assert 1 <= len(symbol) <= 10


def test_synthetic_daily_bar_validation() -> None:
    """Verify SyntheticDailyBar validates OHLC consistency."""
    # Valid bar
    bar = SyntheticDailyBar(
        symbol="THYAO",
        trade_date=date(2026, 1, 5),
        raw_open=Decimal("100.0"),
        raw_high=Decimal("105.0"),
        raw_low=Decimal("98.0"),
        raw_close=Decimal("102.0"),
        adj_open=Decimal("100.0"),
        adj_high=Decimal("105.0"),
        adj_low=Decimal("98.0"),
        adj_close=Decimal("102.0"),
        volume=1_000_000,
        turnover_try=Decimal("102000000.00"),
    )
    assert bar.symbol == "THYAO"
    assert bar.raw_high >= bar.raw_open
    assert bar.raw_low <= bar.raw_close

    # High lower than low should fail
    with pytest.raises(ValueError, match="raw_high .* cannot be less than raw_low"):
        SyntheticDailyBar(
            symbol="THYAO",
            trade_date=date(2026, 1, 5),
            raw_open=Decimal("100.0"),
            raw_high=Decimal("95.0"),
            raw_low=Decimal("98.0"),
            raw_close=Decimal("96.0"),
            adj_open=Decimal("100.0"),
            adj_high=Decimal("95.0"),
            adj_low=Decimal("98.0"),
            adj_close=Decimal("96.0"),
            volume=1000,
            turnover_try=Decimal("96000.00"),
        )


def test_generate_synthetic_ohlcv_skips_weekends_and_is_deterministic() -> None:
    """Verify generate_synthetic_ohlcv skips weekends and produces deterministic output."""
    start = date(2026, 1, 1)  # Thursday
    end = date(2026, 1, 11)  # Sunday

    bars1 = generate_synthetic_ohlcv("ASELS", start, end, seed=123)
    bars2 = generate_synthetic_ohlcv("ASELS", start, end, seed=123)

    assert len(bars1) > 0
    assert len(bars1) == len(bars2)

    # Determinism
    for b1, b2 in zip(bars1, bars2, strict=True):
        assert b1.trade_date == b2.trade_date
        assert b1.raw_close == b2.raw_close
        assert b1.volume == b2.volume
        # No weekends (weekday 5=Sat, 6=Sun)
        assert b1.trade_date.weekday() < 5


def test_generate_synthetic_ohlcv_invalid_dates() -> None:
    """Verify start_date after end_date raises ValueError."""
    with pytest.raises(ValueError, match="cannot be after end_date"):
        generate_synthetic_ohlcv("GARAN", date(2026, 2, 1), date(2026, 1, 1))


def test_generate_synthetic_dataframe() -> None:
    """Verify DataFrame generation contains required columns and correct row count."""
    start = date(2026, 1, 5)  # Monday
    end = date(2026, 1, 9)  # Friday (5 trading days)

    df = generate_synthetic_dataframe("KCHOL", start, end, seed=42)
    assert len(df) == 5
    expected_cols = {
        "symbol",
        "trade_date",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "volume",
        "turnover_try",
        "split_factor",
        "dividend_try",
        "source_name",
        "is_verified",
    }
    assert expected_cols.issubset(set(df.columns))
    assert (df["raw_high"] >= df["raw_low"]).all()
    assert (df["volume"] >= 0).all()
