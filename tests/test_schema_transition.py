"""Tests for Kaggle to Live Provider Schema Transition and Reconciliation (FAZ-10)."""

from datetime import UTC, date
from decimal import Decimal

import pytest

from stock_analysis.pipeline.schema_transition import (
    LiveSchemaTransitionValidator,
    SchemaDifferenceType,
    SchemaDiffSeverity,
    SchemaFieldMapping,
    SchemaTransitionAdapter,
    SchemaTransitionError,
    assert_transition_safe,
)
from stock_analysis.providers.base import DataStatus


def test_schema_comparison_detects_unmapped_and_metadata_missing() -> None:
    validator = LiveSchemaTransitionValidator()
    source_cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "ExtraKaggleFeature"]

    report = validator.compare_schemas(
        dataset_name="kaggle-bist-eod-2026",
        source_columns=source_cols,
    )

    # Extra field is detected as INFO
    extra_diffs = [
        d for d in report.differences if d.diff_type == SchemaDifferenceType.EXTRA_UNMAPPED_FIELD
    ]
    assert len(extra_diffs) == 1
    assert extra_diffs[0].field_name == "ExtraKaggleFeature"
    assert extra_diffs[0].severity == SchemaDiffSeverity.INFO

    # Lacks data_status and source_name metadata
    status_diffs = [
        d
        for d in report.differences
        if d.diff_type == SchemaDifferenceType.METADATA_ABSENT and d.field_name == "data_status"
    ]
    assert len(status_diffs) == 1
    assert status_diffs[0].severity == SchemaDiffSeverity.WARNING

    source_diffs = [
        d
        for d in report.differences
        if d.diff_type == SchemaDifferenceType.METADATA_ABSENT and d.field_name == "source_name"
    ]
    assert len(source_diffs) == 1
    assert source_diffs[0].severity == SchemaDiffSeverity.INFO

    # All mandatory fields are present (assuming default raw adjustment)
    assert report.is_transition_safe is True
    assert report.risk_score < 0.5


def test_schema_comparison_missing_mandatory_fields() -> None:
    validator = LiveSchemaTransitionValidator()
    # Missing Date, Symbol, and Volume
    source_cols = ["Open", "High", "Low", "Close"]

    report = validator.compare_schemas(
        dataset_name="incomplete-dataset",
        source_columns=source_cols,
    )

    assert report.is_transition_safe is False
    assert len(report.critical_issues) >= 2
    assert any("symbol" in issue for issue in report.critical_issues)
    assert any("trade_date" in issue for issue in report.critical_issues)
    assert report.risk_score >= 0.8


def test_schema_comparison_ambiguous_adjustment_convention() -> None:
    validator = LiveSchemaTransitionValidator()
    mapping = SchemaFieldMapping(assumed_adjustment="split_adjusted")
    # Only Close provided, no Adj Close
    source_cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]

    report = validator.compare_schemas(
        dataset_name="ambiguous-adj-dataset",
        source_columns=source_cols,
        mapping=mapping,
    )

    assert report.is_transition_safe is False
    adj_diffs = [
        d
        for d in report.differences
        if d.diff_type == SchemaDifferenceType.ADJUSTMENT_CONVENTION_MISMATCH
    ]
    assert len(adj_diffs) == 1
    assert adj_diffs[0].severity == SchemaDiffSeverity.CRITICAL


def test_inspect_sample_data_warnings_and_date_error() -> None:
    validator = LiveSchemaTransitionValidator()
    source_cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]

    # Sample row with float price and invalid date format
    sample_rows = [
        {
            "Date": "15/01/2026",  # Doesn't match default %Y-%m-%d
            "Symbol": "THYAO",
            "Open": 290.5,
            "High": 295.0,
            "Low": 288.0,
            "Close": 294.0,  # float
            "Volume": 1500000.0,  # float
        }
    ]

    report = validator.compare_schemas(
        dataset_name="sample-format-check",
        source_columns=source_cols,
        sample_rows=sample_rows,
    )

    assert report.is_transition_safe is False
    assert any(d.diff_type == SchemaDifferenceType.TYPE_INCOMPATIBILITY for d in report.differences)
    assert any("Date format parsing failure" in issue for issue in report.critical_issues)


def test_adapter_valid_record_conversion() -> None:
    adapter = SchemaTransitionAdapter()
    raw_row = {
        "Date": "2026-01-15",
        "Symbol": "THYAO",
        "Open": 290.50,
        "High": 295.00,
        "Low": 288.25,
        "Close": 294.00,
        "Volume": 1250000,
    }

    bar = adapter.adapt_record(raw_row)

    assert bar.symbol == "THYAO"
    assert bar.exchange == "BIST"
    assert bar.trade_date == date(2026, 1, 15)
    assert bar.currency == "TRY"
    assert bar.data_status == DataStatus.VERIFIED
    assert bar.raw_open == Decimal("290.5")
    assert bar.raw_high == Decimal("295.0")
    assert bar.raw_low == Decimal("288.25")
    assert bar.raw_close == Decimal("294.0")
    assert bar.adj_close == Decimal("294.0")  # defaulted raw
    assert bar.volume == 1250000
    assert bar.turnover_try == Decimal("1250000") * Decimal("294.0")
    assert bar.session_time_utc is not None
    assert bar.session_time_utc.tzinfo == UTC


def test_adapter_with_adj_close_scaling() -> None:
    adapter = SchemaTransitionAdapter()
    raw_row = {
        "Date": "2026-01-15",
        "Symbol": "GARAN",
        "Open": 100.00,
        "High": 105.00,
        "Low": 98.00,
        "Close": 100.00,
        "Adj Close": 50.00,  # 1:2 split ratio (0.5)
        "Volume": 500000,
    }

    bar = adapter.adapt_record(raw_row)

    assert bar.raw_close == Decimal("100.0")
    assert bar.adj_close == Decimal("50.0")
    assert bar.adj_open == Decimal("50.0000")
    assert bar.adj_high == Decimal("52.5000")
    assert bar.adj_low == Decimal("49.0000")


def test_adapter_with_default_symbol_and_volume_multiplier() -> None:
    mapping = SchemaFieldMapping(volume_multiplier=100)
    adapter = SchemaTransitionAdapter(mapping=mapping, default_symbol="EREGL")

    raw_row = {
        "Date": "2026-01-15",
        "Open": "45.10",
        "High": "46.00",
        "Low": "44.90",
        "Close": "45.80",
        "Volume": "1000",
    }

    bar = adapter.adapt_record(raw_row)

    assert bar.symbol == "EREGL"
    assert bar.volume == 100000  # 1000 * 100 multiplier


def test_adapter_raises_on_missing_symbol_or_date() -> None:
    adapter = SchemaTransitionAdapter()

    # Missing symbol
    with pytest.raises(SchemaTransitionError, match="Missing required 'symbol'"):
        adapter.adapt_record(
            {
                "Date": "2026-01-15",
                "Open": 10,
                "High": 11,
                "Low": 9,
                "Close": 10,
                "Volume": 100,
            }
        )

    # Missing date
    with pytest.raises(SchemaTransitionError, match="Missing required date column"):
        adapter.adapt_record(
            {
                "Symbol": "AKBNK",
                "Open": 10,
                "High": 11,
                "Low": 9,
                "Close": 10,
                "Volume": 100,
            }
        )


def test_adapter_batch_processing() -> None:
    adapter = SchemaTransitionAdapter()
    rows = [
        {
            "Date": "2026-01-15",
            "Symbol": "BIMAS",
            "Open": 500.0,
            "High": 510.0,
            "Low": 495.0,
            "Close": 505.0,
            "Volume": 200000,
        },
        {
            "Date": "2026-01-16",
            "Symbol": "BIMAS",
            "Open": 505.0,
            "High": 515.0,
            "Low": 500.0,
            "Close": 512.0,
            "Volume": 250000,
        },
    ]

    bars = adapter.adapt_batch(rows)
    assert len(bars) == 2
    assert bars[0].trade_date == date(2026, 1, 15)
    assert bars[1].trade_date == date(2026, 1, 16)
    assert bars[1].raw_close == Decimal("512.0")


def test_assert_transition_safe_helper() -> None:
    safe_cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]

    report = assert_transition_safe(
        dataset_name="valid-kaggle-dump",
        source_columns=safe_cols,
    )
    assert report.is_transition_safe is True

    unsafe_cols = ["Date", "Close"]
    with pytest.raises(SchemaTransitionError, match="cannot be safely transitioned"):
        assert_transition_safe(
            dataset_name="invalid-kaggle-dump",
            source_columns=unsafe_cols,
        )
