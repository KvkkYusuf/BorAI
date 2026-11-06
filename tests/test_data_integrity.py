"""Tests for the market data integrity validation module."""

from datetime import date
from decimal import Decimal

import pytest

from stock_analysis.pipeline import (
    DataIssueType,
    validate_market_data_series,
)
from stock_analysis.providers import MarketBarRecord, SyntheticMarketDataProvider

pytestmark = pytest.mark.unit


def _make_bar(
    symbol: str = "THYAO",
    trade_date: date = date(2026, 1, 5),
    raw_open: str = "100.0",
    raw_high: str = "105.0",
    raw_low: str = "98.0",
    raw_close: str = "102.0",
    volume: int = 1_000_000,
    turnover_try: str = "102000000.00",
) -> MarketBarRecord:
    """Helper to construct a valid MarketBarRecord with defaults."""
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        currency="TRY",
        raw_open=Decimal(raw_open),
        raw_high=Decimal(raw_high),
        raw_low=Decimal(raw_low),
        raw_close=Decimal(raw_close),
        adj_open=Decimal(raw_open),
        adj_high=Decimal(raw_high),
        adj_low=Decimal(raw_low),
        adj_close=Decimal(raw_close),
        volume=volume,
        turnover_try=Decimal(turnover_try),
        source_name="test",
        is_verified=True,
    )


def test_empty_series_validation() -> None:
    """Empty series should return valid report with 0 bars."""
    report = validate_market_data_series([])
    assert report.is_valid is True
    assert report.total_bars == 0
    assert len(report.issues) == 0


def test_synthetic_data_integrity() -> None:
    """Synthetic provider output should pass data integrity checks without errors."""
    provider = SyntheticMarketDataProvider(seed=42)
    bars = provider.fetch_daily_bars(
        symbol="THYAO",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
    )
    assert len(bars) > 10

    report = validate_market_data_series(bars)
    assert report.is_valid is True
    assert report.error_count == 0
    assert report.total_bars == len(bars)


def test_duplicate_bar_detection() -> None:
    """Duplicate trade_date in sequence should be flagged as ERROR."""
    bars = [
        _make_bar(trade_date=date(2026, 1, 5)),
        _make_bar(trade_date=date(2026, 1, 5)),
    ]
    report = validate_market_data_series(bars)
    assert report.is_valid is False
    assert report.error_count == 1
    assert any(i.issue_type == DataIssueType.DUPLICATE_BAR for i in report.issues)


def test_out_of_order_detection() -> None:
    """Bars not in strict chronological order should be flagged as ERROR."""
    bars = [
        _make_bar(trade_date=date(2026, 1, 6)),
        _make_bar(trade_date=date(2026, 1, 5)),
    ]
    report = validate_market_data_series(bars)
    assert report.is_valid is False
    assert any(i.issue_type == DataIssueType.OUT_OF_ORDER for i in report.issues)


def test_volume_turnover_mismatch() -> None:
    """Non-zero volume with 0 turnover should trigger a WARNING."""
    bars = [
        _make_bar(trade_date=date(2026, 1, 5), volume=500_000, turnover_try="0.00"),
    ]
    report = validate_market_data_series(bars)
    # WARNING does not invalidate series
    assert report.is_valid is True
    assert report.warning_count == 1
    assert any(i.issue_type == DataIssueType.VOLUME_TURNOVER_MISMATCH for i in report.issues)


def test_volume_spike_anomaly() -> None:
    """Extreme volume spike compared to rolling average should trigger a WARNING."""
    bars = [
        _make_bar(trade_date=date(2026, 1, 5), volume=10_000),
        _make_bar(trade_date=date(2026, 1, 6), volume=12_000),
        _make_bar(trade_date=date(2026, 1, 7), volume=11_000),
        _make_bar(trade_date=date(2026, 1, 8), volume=9_000),
        _make_bar(trade_date=date(2026, 1, 9), volume=10_000),
        # 100x spike
        _make_bar(trade_date=date(2026, 1, 12), volume=1_000_000),
    ]
    report = validate_market_data_series(bars, volume_spike_multiplier=10.0)
    assert report.is_valid is True
    assert any(i.issue_type == DataIssueType.VOLUME_SPIKE_ANOMALY for i in report.issues)


def test_missing_bars_gap_detection() -> None:
    """Missing business days should be flagged when check_gaps=True."""
    bars = [
        _make_bar(trade_date=date(2026, 1, 5)),  # Monday
        # Tuesday 2026-01-06 missing
        _make_bar(trade_date=date(2026, 1, 7)),  # Wednesday
    ]
    report = validate_market_data_series(bars, check_gaps=True)
    assert any(
        i.issue_type == DataIssueType.MISSING_BAR and i.trade_date == date(2026, 1, 6)
        for i in report.issues
    )
