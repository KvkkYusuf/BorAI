"""Unit and anti-leakage compliance tests for time series chronological splitting.

FAZ-10 verification:
- Enforce strict chronological partitioning: Train -> Validation -> Test.
- Forbid random row shuffling (causes severe data leakage in financial time series).
- Detect and prevent look-ahead bias across partition boundaries.
- Support embargo / purge gap between splits.
- Guarantee multi-symbol trading day integrity (cross-sectional isolation).
"""

import random
from datetime import date, datetime, timedelta

import pytest

from stock_analysis.pipeline.kaggle_verifier import TemporalSplitConfig
from stock_analysis.pipeline.time_series_splitter import (
    DataLeakageError,
    SplitPartition,
    TimeSeriesSplitter,
)


def _generate_synthetic_series(
    start_date: date,
    num_days: int,
    symbols: list[str] | None = None,
) -> list[dict[str, object]]:
    """Helper creating sorted multi-symbol daily market records."""
    if symbols is None:
        symbols = ["THYAO", "GARAN", "ASELS"]

    records: list[dict[str, object]] = []
    current = start_date
    for _ in range(num_days):
        for sym in symbols:
            records.append(
                {
                    "symbol": sym,
                    "date": current,
                    "close": 100.0,
                    "volume": 50000,
                }
            )
        current += timedelta(days=1)
    return records


class TestTimeSeriesSplitter:
    """Test suite for anti-leakage chronological time-series dataset splitting."""

    def test_verify_chronological_order_valid(self) -> None:
        """Sequential records should pass chronological verification without error."""
        records = _generate_synthetic_series(date(2024, 1, 1), 10)
        TimeSeriesSplitter.verify_chronological_order(records)

    def test_verify_chronological_order_shuffled_raises_leakage_error(self) -> None:
        """Randomly shuffled records must be flagged immediately as leakage hazard."""
        records = _generate_synthetic_series(date(2024, 1, 1), 20)
        rng = random.Random(42)
        shuffled = list(records)
        rng.shuffle(shuffled)

        with pytest.raises(DataLeakageError, match="Random row shuffling is strictly prohibited"):
            TimeSeriesSplitter.verify_chronological_order(shuffled)

    def test_verify_chronological_order_reverse_sorted_fails(self) -> None:
        """Descending order (reverse chronological) should be rejected."""
        records = _generate_synthetic_series(date(2024, 1, 1), 5)
        reversed_records = list(reversed(records))
        with pytest.raises(DataLeakageError, match="Dataset is not chronologically ordered"):
            TimeSeriesSplitter.verify_chronological_order(reversed_records)

    def test_split_by_config_success(self) -> None:
        """Splitting by valid date boundaries produces isolated train, val, and test subsets."""
        records = _generate_synthetic_series(date(2024, 1, 1), 30)

        config = TemporalSplitConfig(
            train_start=date(2024, 1, 1),
            train_end=date(2024, 1, 15),
            val_start=date(2024, 1, 16),
            val_end=date(2024, 1, 22),
            test_start=date(2024, 1, 23),
            test_end=date(2024, 1, 30),
        )

        train, val, test, summary = TimeSeriesSplitter.split_by_config(records, config)

        assert len(train) == 15 * 3
        assert len(val) == 7 * 3
        assert len(test) == 8 * 3
        assert summary.is_leak_free is True
        assert summary.train_range == (date(2024, 1, 1), date(2024, 1, 15))
        assert summary.val_range == (date(2024, 1, 16), date(2024, 1, 22))
        assert summary.test_range == (date(2024, 1, 23), date(2024, 1, 30))

        # Check partitions summary
        assert len(summary.partitions_summary) == 3
        assert summary.partitions_summary[0].partition == SplitPartition.TRAIN
        assert summary.partitions_summary[0].unique_symbols == 3

    def test_split_by_config_invalid_chronology_raises(self) -> None:
        """Temporal overlapping configurations should be rejected before splitting."""
        records = _generate_synthetic_series(date(2024, 1, 1), 10)
        invalid_config = TemporalSplitConfig(
            train_start=date(2024, 1, 1),
            train_end=date(2024, 1, 5),
            val_start=date(2024, 1, 4),  # Overlaps with train_end
            val_end=date(2024, 1, 7),
            test_start=date(2024, 1, 8),
            test_end=date(2024, 1, 10),
        )
        with pytest.raises(DataLeakageError, match="temporal overlap detected"):
            TimeSeriesSplitter.split_by_config(records, invalid_config)

    def test_split_by_ratios_proportions_and_isolation(self) -> None:
        """Splitting by ratios divides dates chronologically and preserves day integrity."""
        # 100 days of 3 symbols = 300 records
        records = _generate_synthetic_series(date(2024, 1, 1), 100)

        train, val, test, summary = TimeSeriesSplitter.split_by_ratios(
            records,
            train_ratio=0.70,
            val_ratio=0.15,
            test_ratio=0.15,
        )

        assert summary.is_leak_free is True
        assert len(train) == 70 * 3
        assert len(val) == 15 * 3
        assert len(test) == 15 * 3

        # Confirm strict date ordering across boundaries
        assert summary.train_range is not None
        assert summary.val_range is not None
        assert summary.test_range is not None
        assert summary.train_range[1] < summary.val_range[0]
        assert summary.val_range[1] < summary.test_range[0]

    def test_verify_no_leakage_detects_future_contamination(self) -> None:
        """Contamination where train contains future dates compared to val must fail."""
        train = [{"date": date(2024, 1, 10)}, {"date": date(2024, 1, 15)}]
        val = [{"date": date(2024, 1, 14)}]  # Earlier than max train
        test = [{"date": date(2024, 1, 20)}]

        with pytest.raises(DataLeakageError, match="Look-ahead leakage detected"):
            TimeSeriesSplitter.verify_no_leakage(train, val, test)

    def test_embargo_days_enforcement(self) -> None:
        """Embargo buffer period prevents auto-correlation leakage across adjacent days."""
        train = [{"date": date(2024, 1, 10)}]
        # Val starts only 2 days later, but 5 days embargo required
        val = [{"date": date(2024, 1, 12)}]
        test = [{"date": date(2024, 1, 25)}]

        with pytest.raises(DataLeakageError, match="Embargo violation"):
            TimeSeriesSplitter.verify_no_leakage(train, val, test, embargo_days=5)

        # Passes when embargo gap is satisfied
        val_compliant = [{"date": date(2024, 1, 16)}]
        assert (
            TimeSeriesSplitter.verify_no_leakage(train, val_compliant, test, embargo_days=5) is True
        )

    def test_supports_datetime_and_objects(self) -> None:
        """Extracts date correctly from objects and datetime timestamps."""

        class DummyBar:
            def __init__(self, dt: datetime, sym: str) -> None:
                self.timestamp = dt
                self.symbol = sym

        bars = [
            DummyBar(datetime(2024, 1, 1, 18, 0), "THYAO"),
            DummyBar(datetime(2024, 1, 2, 18, 0), "THYAO"),
            DummyBar(datetime(2024, 1, 3, 18, 0), "THYAO"),
        ]

        train, val, test, summary = TimeSeriesSplitter.split_by_ratios(
            bars,
            train_ratio=0.34,
            val_ratio=0.33,
            test_ratio=0.33,
        )
        assert len(train) == 1
        assert len(val) == 1
        assert len(test) == 1
        assert summary.is_leak_free is True
