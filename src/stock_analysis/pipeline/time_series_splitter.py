"""Time series chronological dataset splitting and leakage prevention module.

Enforces strict chronological partitioning for machine learning and analytics (FAZ-10):
- Prevents random row-level shuffling (causes severe look-ahead leakage in financial time series).
- Strict chronological boundary separation: Train < Validation < Test.
- Support for embargo / purge gap windows to prevent label overlap and auto-correlation leakage.
- Direct verification and audit methods ensuring zero future-data contamination.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.kaggle_verifier import TemporalSplitConfig


class SplitPartition(StrEnum):
    """Partitions for time series model validation."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class DataLeakageError(ValueError):
    """Raised when temporal chronology or look-ahead isolation is violated."""


class SplitPartitionMetrics(BaseModel):
    """Summary metrics for a partitioned dataset segment."""

    model_config = ConfigDict(frozen=True)

    partition: SplitPartition
    record_count: int
    start_date: date | None = None
    end_date: date | None = None
    unique_symbols: int = 0


class SplitDatasetResult(BaseModel):
    """Structured result of a temporal chronological split."""

    model_config = ConfigDict(frozen=True)

    train_count: int
    val_count: int
    test_count: int
    train_range: tuple[date, date] | None = None
    val_range: tuple[date, date] | None = None
    test_range: tuple[date, date] | None = None
    embargo_days: int = 0
    is_leak_free: bool = True
    partitions_summary: list[SplitPartitionMetrics] = Field(default_factory=list)


def _extract_record_date(record: Any) -> date:
    """Extract a date object from either an object attribute or dictionary key."""
    if isinstance(record, dict):
        val = record.get("date") or record.get("timestamp")
    else:
        val = getattr(record, "date", None) or getattr(record, "timestamp", None)

    if val is None:
        raise ValueError(f"Record does not contain 'date' or 'timestamp': {record!r}")

    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if hasattr(val, "date") and callable(val.date):
        res = val.date()
        if isinstance(res, date):
            return res
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    raise TypeError(f"Unsupported date format for time series item: {type(val)}")


def _extract_symbol(record: Any) -> str | None:
    """Extract ticker/symbol if present."""
    if isinstance(record, dict):
        return record.get("symbol") or record.get("ticker")
    return getattr(record, "symbol", None) or getattr(record, "ticker", None)


class TimeSeriesSplitter:
    """Chronological time-series dataset splitter with anti-leakage guards."""

    @staticmethod
    def verify_chronological_order(records: Sequence[Any]) -> None:
        """Verify that records are strictly sorted by date in non-decreasing order.

        Randomly shuffled financial time series violate stationarity assumptions and leak
        future information across folds.
        """
        if len(records) <= 1:
            return

        prev_dt = _extract_record_date(records[0])
        for idx, rec in enumerate(records[1:], start=1):
            curr_dt = _extract_record_date(rec)
            if curr_dt < prev_dt:
                raise DataLeakageError(
                    f"Dataset is not chronologically ordered at index {idx} "
                    f"({curr_dt} < {prev_dt}). Random row shuffling is strictly prohibited "
                    "in financial time-series forecasting."
                )
            prev_dt = curr_dt

    @staticmethod
    def verify_no_leakage(
        train_records: Sequence[Any],
        val_records: Sequence[Any],
        test_records: Sequence[Any],
        embargo_days: int = 0,
    ) -> bool:
        """Verify that train, validation, and test partitions have zero look-ahead contamination."""
        if not train_records:
            raise DataLeakageError("Train partition cannot be empty.")

        train_dates = [_extract_record_date(r) for r in train_records]
        max_train_date = max(train_dates)

        if val_records:
            val_dates = [_extract_record_date(r) for r in val_records]
            min_val_date = min(val_dates)
            required_val_start = max_train_date + timedelta(days=embargo_days)
            if min_val_date <= required_val_start and embargo_days > 0:
                raise DataLeakageError(
                    f"Embargo violation between train and val: max_train={max_train_date}, "
                    f"min_val={min_val_date}, embargo_days={embargo_days}."
                )
            if min_val_date <= max_train_date:
                raise DataLeakageError(
                    f"Look-ahead leakage detected: max train date ({max_train_date}) >= "
                    f"min val date ({min_val_date})."
                )
            max_val_date = max(val_dates)
        else:
            max_val_date = max_train_date

        if test_records:
            test_dates = [_extract_record_date(r) for r in test_records]
            min_test_date = min(test_dates)
            required_test_start = max_val_date + timedelta(days=embargo_days)
            if min_test_date <= required_test_start and embargo_days > 0:
                raise DataLeakageError(
                    f"Embargo violation between val and test: max_val={max_val_date}, "
                    f"min_test={min_test_date}, embargo_days={embargo_days}."
                )
            if min_test_date <= max_val_date:
                raise DataLeakageError(
                    f"Look-ahead leakage detected: max prior partition date ({max_val_date}) >= "
                    f"min test date ({min_test_date})."
                )

        return True

    @classmethod
    def split_by_config(
        cls,
        records: Sequence[Any],
        config: TemporalSplitConfig,
        embargo_days: int = 0,
    ) -> tuple[list[Any], list[Any], list[Any], SplitDatasetResult]:
        """Split a chronological record sequence using explicit date boundaries."""
        chrono_errors = config.validate_chronology()
        if chrono_errors:
            err_msg = ", ".join(chrono_errors)
            raise DataLeakageError(f"Invalid temporal split configuration: {err_msg}")

        cls.verify_chronological_order(records)

        train: list[Any] = []
        val: list[Any] = []
        test: list[Any] = []

        for r in records:
            dt = _extract_record_date(r)
            if config.train_start <= dt <= config.train_end:
                train.append(r)
            elif config.val_start <= dt <= config.val_end:
                val.append(r)
            elif config.test_start <= dt <= config.test_end:
                test.append(r)

        cls.verify_no_leakage(train, val, test, embargo_days=embargo_days)

        summary = cls._build_summary(train, val, test, embargo_days=embargo_days)
        return train, val, test, summary

    @classmethod
    def split_by_ratios(
        cls,
        records: Sequence[Any],
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        embargo_days: int = 0,
    ) -> tuple[list[Any], list[Any], list[Any], SplitDatasetResult]:
        """Chronologically partition records according to target time-window proportions.

        Partitions are created by unique calendar dates to ensure multi-symbol panels
        are not split across cross-sections of the same trading day.
        """
        if not (0.999 <= (train_ratio + val_ratio + test_ratio) <= 1.001):
            raise ValueError("Partition ratios must sum to 1.0")
        if train_ratio <= 0 or val_ratio < 0 or test_ratio <= 0:
            raise ValueError("Train and test ratios must be positive.")

        cls.verify_chronological_order(records)

        # Collect unique sorted trading dates
        unique_dates = sorted({_extract_record_date(r) for r in records})
        n_dates = len(unique_dates)

        if n_dates < 3:
            raise ValueError(
                f"Insufficient unique dates ({n_dates}) for 3-way time series splitting."
            )

        train_len = max(1, int(n_dates * train_ratio))
        val_len = max(1, int(n_dates * val_ratio)) if val_ratio > 0 else 0

        train_dates = set(unique_dates[:train_len])
        raw_val_dates = set(unique_dates[train_len : train_len + val_len])
        raw_test_dates = set(unique_dates[train_len + val_len :])

        if embargo_days > 0 and train_dates:
            max_train = max(train_dates)
            val_dates = {d for d in raw_val_dates if d > max_train + timedelta(days=embargo_days)}
            if val_dates:
                max_val = max(val_dates)
                test_dates = {
                    d for d in raw_test_dates if d > max_val + timedelta(days=embargo_days)
                }
            else:
                test_dates = {
                    d for d in raw_test_dates if d > max_train + timedelta(days=embargo_days)
                }
        else:
            val_dates = raw_val_dates
            test_dates = raw_test_dates

        train = [r for r in records if _extract_record_date(r) in train_dates]
        val = [r for r in records if _extract_record_date(r) in val_dates]
        test = [r for r in records if _extract_record_date(r) in test_dates]

        cls.verify_no_leakage(train, val, test, embargo_days=embargo_days)

        summary = cls._build_summary(train, val, test, embargo_days=embargo_days)
        return train, val, test, summary

    @staticmethod
    def _build_summary(
        train: Sequence[Any],
        val: Sequence[Any],
        test: Sequence[Any],
        embargo_days: int,
    ) -> SplitDatasetResult:
        """Generate structured audit summary of the split."""
        partitions: list[SplitPartitionMetrics] = []

        def partition_meta(part: SplitPartition, recs: Sequence[Any]) -> SplitPartitionMetrics:
            if not recs:
                return SplitPartitionMetrics(partition=part, record_count=0)
            dates = [_extract_record_date(r) for r in recs]
            symbols = {_extract_symbol(r) for r in recs if _extract_symbol(r) is not None}
            return SplitPartitionMetrics(
                partition=part,
                record_count=len(recs),
                start_date=min(dates),
                end_date=max(dates),
                unique_symbols=len(symbols),
            )

        train_p = partition_meta(SplitPartition.TRAIN, train)
        val_p = partition_meta(SplitPartition.VALIDATION, val)
        test_p = partition_meta(SplitPartition.TEST, test)

        partitions.extend([train_p, val_p, test_p])

        train_rng = (
            (train_p.start_date, train_p.end_date)
            if train_p.start_date and train_p.end_date
            else None
        )
        val_rng = (
            (val_p.start_date, val_p.end_date) if val_p.start_date and val_p.end_date else None
        )
        test_rng = (
            (test_p.start_date, test_p.end_date) if test_p.start_date and test_p.end_date else None
        )

        return SplitDatasetResult(
            train_count=len(train),
            val_count=len(val),
            test_count=len(test),
            train_range=train_rng,
            val_range=val_rng,
            test_range=test_rng,
            embargo_days=embargo_days,
            is_leak_free=True,
            partitions_summary=partitions,
        )
