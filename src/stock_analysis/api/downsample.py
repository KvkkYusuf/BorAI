"""Downsampling algorithms and utilities for financial time series and chart feeds."""

from __future__ import annotations

from collections.abc import Sequence

from stock_analysis.api.schemas import CandleItem

VALID_DOWNSAMPLE_METHODS: frozenset[str] = frozenset({"bucket", "weekly", "monthly"})


def aggregate_candle_bucket(candles: Sequence[CandleItem]) -> CandleItem:
    """Aggregate a sequence of chronologically ordered OHLCV candles into a single bar.

    Preserves standard financial bar properties:
    - date: Date of the last candle in the bucket (representing end-of-period bar)
    - open: Open price of the first candle
    - high: Highest price across all candles in the bucket
    - low: Lowest price across all candles in the bucket
    - close: Close price of the last candle
    - volume: Sum of volumes
    - turnover_try: Sum of turnover values (if available)
    """
    if not candles:
        raise ValueError("Cannot aggregate an empty sequence of candles")

    first = candles[0]
    last = candles[-1]

    highest = max(c.high for c in candles)
    lowest = min(c.low for c in candles)
    total_volume = round(sum(c.volume for c in candles), 4)

    turnovers = [c.turnover_try for c in candles if c.turnover_try is not None]
    total_turnover = round(sum(turnovers), 2) if turnovers else None

    return CandleItem(
        date=last.date,
        open=first.open,
        high=highest,
        low=lowest,
        close=last.close,
        volume=total_volume,
        turnover_try=total_turnover,
    )


def downsample_bucket(candles: list[CandleItem], max_points: int) -> list[CandleItem]:
    """Downsample candle series to at most `max_points` using contiguous time buckets."""
    n = len(candles)
    if n <= max_points or max_points <= 0:
        return list(candles)

    downsampled: list[CandleItem] = []
    for i in range(max_points):
        start_idx = (i * n) // max_points
        end_idx = ((i + 1) * n) // max_points
        bucket = candles[start_idx:end_idx]
        if bucket:
            downsampled.append(aggregate_candle_bucket(bucket))

    return downsampled


def downsample_weekly(candles: list[CandleItem]) -> list[CandleItem]:
    """Aggregate daily candles into weekly bars based on ISO calendar (year, week)."""
    if not candles:
        return []

    groups: list[list[CandleItem]] = []
    current_key: tuple[int, int] | None = None
    current_group: list[CandleItem] = []

    for candle in candles:
        iso_year, iso_week, _ = candle.date.isocalendar()
        key = (iso_year, iso_week)
        if key != current_key:
            if current_group:
                groups.append(current_group)
            current_group = [candle]
            current_key = key
        else:
            current_group.append(candle)

    if current_group:
        groups.append(current_group)

    return [aggregate_candle_bucket(grp) for grp in groups]


def downsample_monthly(candles: list[CandleItem]) -> list[CandleItem]:
    """Aggregate daily candles into monthly bars based on calendar (year, month)."""
    if not candles:
        return []

    groups: list[list[CandleItem]] = []
    current_key: tuple[int, int] | None = None
    current_group: list[CandleItem] = []

    for candle in candles:
        key = (candle.date.year, candle.date.month)
        if key != current_key:
            if current_group:
                groups.append(current_group)
            current_group = [candle]
            current_key = key
        else:
            current_group.append(candle)

    if current_group:
        groups.append(current_group)

    return [aggregate_candle_bucket(grp) for grp in groups]


def downsample_series(
    candles: list[CandleItem],
    *,
    method: str | None = None,
    max_points: int | None = None,
) -> tuple[list[CandleItem], bool, str | None]:
    """Apply chart downsampling if requested and appropriate.

    Returns:
        (result_candles, is_downsampled, effective_method)
    """
    if not candles:
        return [], False, None

    if method is None and max_points is None:
        return candles, False, None

    effective_method = method or "bucket"

    if effective_method == "weekly":
        result = downsample_weekly(candles)
        if max_points is not None and len(result) > max_points:
            result = downsample_bucket(result, max_points)
        return result, True, "weekly"

    if effective_method == "monthly":
        result = downsample_monthly(candles)
        if max_points is not None and len(result) > max_points:
            result = downsample_bucket(result, max_points)
        return result, True, "monthly"

    if effective_method == "bucket":
        target_points = max_points or 100
        if len(candles) <= target_points:
            return candles, False, None
        result = downsample_bucket(candles, target_points)
        return result, True, "bucket"

    raise ValueError(f"Unknown downsample method: {method}")
