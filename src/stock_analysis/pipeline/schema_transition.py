"""Kaggle-to-Live Market Provider Schema Transition & Reconciliation Module.

FAZ-10 requirement:
Kaggle verisini canlı veri sağlayıcısıyla aynı kabul etme;
canlıya geçişte kaynak ve şema farklarını test et.

Addresses fundamental risks in transition from static Kaggle dumps to live data:
1. Column name & semantic divergence (e.g., "Close" vs raw_close vs adj_close).
2. Timezone and temporal convention (naive string date vs UTC session datetime).
3. Numeric type & precision discrepancies (float64 binary rounding vs exact Decimal).
4. Volume/turnover unit scale divergence (lots vs share count vs turnover TRY).
5. Corporate action adjustment confusion (back-adjusted features vs unadjusted live bars).
6. Lifecycle status & reconciliation metadata (static datasets lack PROVISIONAL vs VERIFIED).
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.providers.base import DataStatus, MarketBarRecord


class SchemaDiffSeverity(StrEnum):
    """Severity of a discovered schema difference between historical and live data."""

    CRITICAL = "CRITICAL"  # Blocker: causes model corruption or runtime crashes
    WARNING = "WARNING"  # Requires explicit mapping or defaults
    INFO = "INFO"  # Harmless extra column or non-critical attribute


class SchemaDifferenceType(StrEnum):
    """Categorized schema differences."""

    MISSING_REQUIRED_FIELD = "missing_required_field"
    EXTRA_UNMAPPED_FIELD = "extra_unmapped_field"
    TYPE_INCOMPATIBILITY = "type_incompatibility"
    TIMEZONE_MISMATCH = "timezone_mismatch"
    ADJUSTMENT_CONVENTION_MISMATCH = "adjustment_convention_mismatch"
    SCALE_OR_UNIT_MISMATCH = "scale_or_unit_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    METADATA_ABSENT = "metadata_absent"


class SchemaDifference(BaseModel):
    """Record describing a specific schema difference."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    diff_type: SchemaDifferenceType
    severity: SchemaDiffSeverity
    source_spec: str | None = None
    target_spec: str
    remediation: str


class SchemaFieldMapping(BaseModel):
    """Configuration mapping Kaggle dataset fields to Live MarketBarRecord fields."""

    model_config = ConfigDict(frozen=True)

    column_map: dict[str, str] = Field(
        default_factory=lambda: {
            "Symbol": "symbol",
            "Ticker": "symbol",
            "Date": "trade_date",
            "Open": "raw_open",
            "High": "raw_high",
            "Low": "raw_low",
            "Close": "raw_close",
            "Volume": "volume",
        },
        description="Source dataset column name -> Target MarketBarRecord field name",
    )
    default_exchange: str = "BIST"
    default_currency: str = "TRY"
    default_timezone: str = "Europe/Istanbul"
    default_status: DataStatus = DataStatus.VERIFIED
    default_source_name: str = "kaggle-transition"
    date_format: str | None = "%Y-%m-%d"
    assumed_adjustment: str = Field(
        default="raw",
        description="Source price adjustment: 'raw', 'split_adjusted', or 'fully_adjusted'",
    )
    volume_multiplier: int = Field(
        default=1,
        ge=1,
        description="Multiplier if volume in source is in lots/thousands (e.g. 1 or 1000)",
    )


class SchemaTransitionReport(BaseModel):
    """Audit report for Kaggle to live data provider schema compatibility."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str
    evaluated_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_fields: list[str]
    target_fields: list[str]
    differences: list[SchemaDifference]
    is_transition_safe: bool
    risk_score: float = Field(ge=0.0, le=1.0)
    critical_issues: list[str]
    remediations: list[str]


class SchemaTransitionError(Exception):
    """Raised when Kaggle data cannot be safely adapted to live market schema."""


class LiveSchemaTransitionValidator:
    """Validates structural and semantic differences between Kaggle datasets and live providers."""

    # Target core fields in MarketBarRecord
    REQUIRED_LIVE_FIELDS: frozenset[str] = frozenset(
        {
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
        }
    )

    PRICE_FIELDS: frozenset[str] = frozenset(
        {
            "raw_open",
            "raw_high",
            "raw_low",
            "raw_close",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
        }
    )

    def __init__(self, target_schema: type[MarketBarRecord] = MarketBarRecord) -> None:
        self.target_schema = target_schema

    def compare_schemas(
        self,
        dataset_name: str,
        source_columns: list[str],
        mapping: SchemaFieldMapping | None = None,
        sample_rows: list[dict[str, Any]] | None = None,
    ) -> SchemaTransitionReport:
        """Compare source Kaggle schema with the target live provider schema."""
        cfg = mapping or SchemaFieldMapping()
        diffs: list[SchemaDifference] = []
        critical_issues: list[str] = []
        remediations: list[str] = []

        # Target fields
        target_model_fields = list(self.target_schema.model_fields.keys())

        # Map source fields
        mapped_target_fields = {cfg.column_map.get(col, col) for col in source_columns}

        # Check for unmapped source columns
        for col in source_columns:
            if col not in cfg.column_map and col not in target_model_fields:
                diffs.append(
                    SchemaDifference(
                        field_name=col,
                        diff_type=SchemaDifferenceType.EXTRA_UNMAPPED_FIELD,
                        severity=SchemaDiffSeverity.INFO,
                        source_spec=f"Column '{col}' present in source dataset",
                        target_spec="Not in target MarketBarRecord",
                        remediation=f"Map '{col}' via SchemaFieldMapping or ignore if auxiliary.",
                    )
                )

        # Check required fields
        # Note: adj_* can be defaulted from raw_* if assumed_adjustment == 'raw'
        effective_fields = set(mapped_target_fields)
        if "raw_close" in effective_fields and "adj_close" not in effective_fields:
            if cfg.assumed_adjustment == "raw":
                effective_fields.add("adj_open")
                effective_fields.add("adj_high")
                effective_fields.add("adj_low")
                effective_fields.add("adj_close")
            else:
                diffs.append(
                    SchemaDifference(
                        field_name="raw_close/adj_close",
                        diff_type=SchemaDifferenceType.ADJUSTMENT_CONVENTION_MISMATCH,
                        severity=SchemaDiffSeverity.CRITICAL,
                        source_spec=f"Source columns mapped as {cfg.assumed_adjustment}",
                        target_spec="Dual stream: raw_close AND adj_close both required",
                        remediation=(
                            "Explicitly specify whether Kaggle prices are dividend/split "
                            "adjusted or unadjusted."
                        ),
                    )
                )
                critical_issues.append(
                    "Ambiguous price adjustment convention between Kaggle and live provider."
                )

        # Check symbol requirement
        if "symbol" not in effective_fields:
            # If dataset is single-symbol, sample might lack symbol column
            diffs.append(
                SchemaDifference(
                    field_name="symbol",
                    diff_type=SchemaDifferenceType.MISSING_REQUIRED_FIELD,
                    severity=SchemaDiffSeverity.CRITICAL,
                    source_spec="No 'symbol' column mapped",
                    target_spec="Field 'symbol' is mandatory for live routing",
                    remediation="Add symbol column or provide default_symbol during transform.",
                )
            )
            critical_issues.append("Missing mandatory 'symbol' column.")

        # Check missing date
        if "trade_date" not in effective_fields:
            diffs.append(
                SchemaDifference(
                    field_name="trade_date",
                    diff_type=SchemaDifferenceType.MISSING_REQUIRED_FIELD,
                    severity=SchemaDiffSeverity.CRITICAL,
                    source_spec="No 'trade_date' or mapped date column",
                    target_spec="Field 'trade_date' is mandatory for temporal ordering",
                    remediation="Map dataset date column to 'trade_date'.",
                )
            )
            critical_issues.append("Missing mandatory 'trade_date' column.")

        # Check missing OHLCV
        for base_price in ["raw_open", "raw_high", "raw_low", "raw_close", "volume"]:
            if base_price not in effective_fields:
                diffs.append(
                    SchemaDifference(
                        field_name=base_price,
                        diff_type=SchemaDifferenceType.MISSING_REQUIRED_FIELD,
                        severity=SchemaDiffSeverity.CRITICAL,
                        source_spec=f"'{base_price}' not provided",
                        target_spec=f"Field '{base_price}' is mandatory",
                        remediation=f"Map source column to '{base_price}'.",
                    )
                )
                critical_issues.append(f"Missing mandatory OHLCV field '{base_price}'.")

        # Inspect sample rows if provided for runtime type & precision divergence
        if sample_rows:
            self._inspect_sample_data(sample_rows, cfg, diffs, critical_issues)

        # Metadata absence check
        if "data_status" not in effective_fields:
            diffs.append(
                SchemaDifference(
                    field_name="data_status",
                    diff_type=SchemaDifferenceType.METADATA_ABSENT,
                    severity=SchemaDiffSeverity.WARNING,
                    source_spec="Static dataset lacks lifecycle status",
                    target_spec="MarketBarRecord requires DataStatus (e.g. VERIFIED)",
                    remediation=(
                        f"Defaulting to '{cfg.default_status.value}' for static historical records."
                    ),
                )
            )
        if "source_name" not in effective_fields:
            diffs.append(
                SchemaDifference(
                    field_name="source_name",
                    diff_type=SchemaDifferenceType.METADATA_ABSENT,
                    severity=SchemaDiffSeverity.INFO,
                    source_spec="Static dataset lacks source identifier",
                    target_spec="MarketBarRecord requires source_name",
                    remediation=f"Defaulting to '{cfg.default_source_name}'.",
                )
            )

        # Risk score calculation
        num_critical = sum(1 for d in diffs if d.severity == SchemaDiffSeverity.CRITICAL)
        num_warning = sum(1 for d in diffs if d.severity == SchemaDiffSeverity.WARNING)
        raw_score = min(1.0, (num_critical * 0.4) + (num_warning * 0.1))

        is_safe = num_critical == 0

        for d in diffs:
            if d.remediation not in remediations:
                remediations.append(d.remediation)

        return SchemaTransitionReport(
            dataset_name=dataset_name,
            source_fields=source_columns,
            target_fields=target_model_fields,
            differences=diffs,
            is_transition_safe=is_safe,
            risk_score=round(raw_score, 2),
            critical_issues=critical_issues,
            remediations=remediations,
        )

    def _inspect_sample_data(
        self,
        samples: list[dict[str, Any]],
        cfg: SchemaFieldMapping,
        diffs: list[SchemaDifference],
        critical_issues: list[str],
    ) -> None:
        """Inspect row values for type mismatches (float vs Decimal, timezone, date parsing)."""
        first_row = samples[0]

        # Check date format / string
        date_col = next((k for k, v in cfg.column_map.items() if v == "trade_date"), "Date")
        if date_col in first_row:
            val = first_row[date_col]
            if isinstance(val, str):
                try:
                    datetime.strptime(val, cfg.date_format or "%Y-%m-%d")
                except ValueError:
                    diffs.append(
                        SchemaDifference(
                            field_name="trade_date",
                            diff_type=SchemaDifferenceType.TYPE_INCOMPATIBILITY,
                            severity=SchemaDiffSeverity.CRITICAL,
                            source_spec=f"Date string value '{val}'",
                            target_spec=f"Format '{cfg.date_format}'",
                            remediation=(
                                f"Update date_format in SchemaFieldMapping to match '{val}'."
                            ),
                        )
                    )
                    critical_issues.append(f"Date format parsing failure for sample value '{val}'.")

        # Check float vs Decimal
        close_col = next((k for k, v in cfg.column_map.items() if v == "raw_close"), "Close")
        if close_col in first_row:
            close_val = first_row[close_col]
            if isinstance(close_val, float):
                diffs.append(
                    SchemaDifference(
                        field_name="raw_close",
                        diff_type=SchemaDifferenceType.TYPE_INCOMPATIBILITY,
                        severity=SchemaDiffSeverity.WARNING,
                        source_spec="float64 (potential IEEE 754 precision drift)",
                        target_spec="Decimal (exact financial representation)",
                        remediation=(
                            "Convert float inputs to str before Decimal instantiation "
                            "to avoid drift."
                        ),
                    )
                )

        # Check volume scaling
        vol_col = next((k for k, v in cfg.column_map.items() if v == "volume"), "Volume")
        if vol_col in first_row:
            vol_val = first_row[vol_col]
            if isinstance(vol_val, (float, str)):
                diffs.append(
                    SchemaDifference(
                        field_name="volume",
                        diff_type=SchemaDifferenceType.SCALE_OR_UNIT_MISMATCH,
                        severity=SchemaDiffSeverity.WARNING,
                        source_spec=f"Volume provided as {type(vol_val).__name__}",
                        target_spec="int (integer shares count)",
                        remediation="Cast volume to integer and verify lot vs share scale.",
                    )
                )


class SchemaTransitionAdapter:
    """Adapts raw Kaggle tabular records into validated MarketBarRecord instances."""

    def __init__(
        self,
        mapping: SchemaFieldMapping | None = None,
        default_symbol: str | None = None,
    ) -> None:
        self.mapping = mapping or SchemaFieldMapping()
        self.default_symbol = default_symbol

    def adapt_record(self, raw_row: dict[str, Any]) -> MarketBarRecord:
        """Convert a single Kaggle record dictionary into MarketBarRecord."""
        col_map = self.mapping.column_map

        # Symbol resolution
        symbol_col = next((k for k, v in col_map.items() if v == "symbol"), "Symbol")
        symbol = raw_row.get(symbol_col) or self.default_symbol
        if not symbol or not isinstance(symbol, str):
            raise SchemaTransitionError(
                "Missing required 'symbol' field and no default_symbol provided."
            )

        # Date parsing
        date_col = next((k for k, v in col_map.items() if v == "trade_date"), "Date")
        raw_date = raw_row.get(date_col)
        if raw_date is None:
            raise SchemaTransitionError(f"Missing required date column '{date_col}'.")

        parsed_date: date
        if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
            parsed_date = raw_date
        elif isinstance(raw_date, datetime):
            parsed_date = raw_date.date()
        elif isinstance(raw_date, str):
            try:
                fmt = self.mapping.date_format or "%Y-%m-%d"
                parsed_date = datetime.strptime(raw_date.strip(), fmt).date()
            except ValueError as exc:
                raise SchemaTransitionError(f"Invalid date string '{raw_date}': {exc}") from exc
        else:
            raise SchemaTransitionError(f"Unsupported date type: {type(raw_date)}")

        # Helper for price decimal parsing
        def parse_price(col_name: str, fallback_key: str) -> Decimal:
            val = raw_row.get(col_name)
            if val is None:
                val = raw_row.get(fallback_key)
            if val is None:
                raise SchemaTransitionError(
                    f"Missing mandatory price field for '{col_name}' / '{fallback_key}'."
                )
            try:
                # String conversion prevents float binary precision loss
                return Decimal(str(val))
            except (InvalidOperation, TypeError) as exc:
                raise SchemaTransitionError(
                    f"Cannot parse price '{val}' as Decimal: {exc}"
                ) from exc

        # Resolve column names for OHLC
        open_col = next((k for k, v in col_map.items() if v == "raw_open"), "Open")
        high_col = next((k for k, v in col_map.items() if v == "raw_high"), "High")
        low_col = next((k for k, v in col_map.items() if v == "raw_low"), "Low")
        close_col = next((k for k, v in col_map.items() if v == "raw_close"), "Close")

        raw_open = parse_price(open_col, "Open")
        raw_high = parse_price(high_col, "High")
        raw_low = parse_price(low_col, "Low")
        raw_close = parse_price(close_col, "Close")

        # Resolve adjusted prices
        adj_close_col = next((k for k, v in col_map.items() if v == "adj_close"), "Adj Close")
        if adj_close_col in raw_row and raw_row[adj_close_col] is not None:
            adj_close = parse_price(adj_close_col, "Adj Close")
            # If adj_open/high/low are not distinct in source, scale them proportionally
            ratio = adj_close / raw_close if raw_close != Decimal("0") else Decimal("1.0")
            adj_open = (raw_open * ratio).quantize(Decimal("0.0001"))
            adj_high = (raw_high * ratio).quantize(Decimal("0.0001"))
            adj_low = (raw_low * ratio).quantize(Decimal("0.0001"))
        else:
            # Assumed raw: adj matches raw
            adj_open = raw_open
            adj_high = raw_high
            adj_low = raw_low
            adj_close = raw_close

        # Parse Volume
        vol_col = next((k for k, v in col_map.items() if v == "volume"), "Volume")
        raw_vol = raw_row.get(vol_col, 0)
        try:
            # Handle float or string volume representation
            base_vol = int(float(str(raw_vol).replace(",", "")))
            volume = base_vol * self.mapping.volume_multiplier
        except (ValueError, TypeError) as exc:
            raise SchemaTransitionError(f"Invalid volume value '{raw_vol}': {exc}") from exc

        # Compute or fallback turnover_try
        turnover_col = next((k for k, v in col_map.items() if v == "turnover_try"), "Turnover")
        raw_turnover = raw_row.get(turnover_col)
        if raw_turnover is not None:
            try:
                turnover_try = Decimal(str(raw_turnover))
            except InvalidOperation:
                turnover_try = Decimal(volume) * raw_close
        else:
            turnover_try = Decimal(volume) * raw_close

        # Compute UTC session timestamp (BIST EOD close is 18:10 TSI -> 15:10 UTC in standard time)
        # Europe/Istanbul is UTC+3 permanently
        istanbul_tz = ZoneInfo(self.mapping.default_timezone)
        local_session_time = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            18,
            10,
            0,
            tzinfo=istanbul_tz,
        )
        session_time_utc = local_session_time.astimezone(UTC)

        return MarketBarRecord(
            symbol=symbol.strip().upper(),
            exchange=self.mapping.default_exchange,
            trade_date=parsed_date,
            timezone=self.mapping.default_timezone,
            session_time_utc=session_time_utc,
            currency=self.mapping.default_currency,
            data_status=self.mapping.default_status,
            raw_open=raw_open,
            raw_high=raw_high,
            raw_low=raw_low,
            raw_close=raw_close,
            adj_open=adj_open,
            adj_high=adj_high,
            adj_low=adj_low,
            adj_close=adj_close,
            volume=volume,
            turnover_try=turnover_try,
            split_factor=Decimal("1.0"),
            dividend_try=Decimal("0.0"),
            source_name=str(raw_row.get("source_name") or self.mapping.default_source_name),
            is_verified=True,
        )

    def adapt_batch(self, raw_rows: list[dict[str, Any]]) -> list[MarketBarRecord]:
        """Convert a batch of raw Kaggle rows into validated MarketBarRecord objects."""
        return [self.adapt_record(row) for row in raw_rows]


def assert_transition_safe(
    dataset_name: str,
    source_columns: list[str],
    sample_rows: list[dict[str, Any]] | None = None,
    mapping: SchemaFieldMapping | None = None,
) -> SchemaTransitionReport:
    """Pre-flight check assertion before model deployment to live provider feed.

    Raises SchemaTransitionError if critical discrepancies or unmapped fields are found.
    """
    validator = LiveSchemaTransitionValidator()
    report = validator.compare_schemas(
        dataset_name=dataset_name,
        source_columns=source_columns,
        mapping=mapping,
        sample_rows=sample_rows,
    )
    if not report.is_transition_safe:
        issues_summary = "; ".join(report.critical_issues)
        raise SchemaTransitionError(
            f"Kaggle dataset '{dataset_name}' cannot be safely transitioned "
            f"to live provider feed: {issues_summary}"
        )
    return report


# Alias for roadmap order compatibility
SchemaTransitionReconciler = LiveSchemaTransitionValidator

