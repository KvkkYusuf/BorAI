"""Market data integrity validation module for checking EOD price bars."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from stock_analysis.pipeline.calendar import BaseExchangeCalendar
from stock_analysis.providers.base import MarketBarRecord


class IssueSeverity(StrEnum):
    """Severity of a detected data integrity issue."""

    ERROR = "error"  # Critical flaw that invalidates the bar or series
    WARNING = "warning"  # Statistical or temporal anomaly warranting review


class DataIssueType(StrEnum):
    """Categorized data anomaly types."""

    DUPLICATE_BAR = "duplicate_bar"
    OUT_OF_ORDER = "out_of_order"
    MISSING_BAR = "missing_bar"
    NON_POSITIVE_PRICE = "non_positive_price"
    INCONSISTENT_OHLC = "inconsistent_ohlc"
    NEGATIVE_VOLUME = "negative_volume"
    VOLUME_TURNOVER_MISMATCH = "volume_turnover_mismatch"
    VOLUME_SPIKE_ANOMALY = "volume_spike_anomaly"
    INCONSISTENT_SPLIT_FACTOR = "inconsistent_split_factor"
    UNEXPECTED_HOLIDAY_BAR = "unexpected_holiday_bar"


class DataIntegrityIssue(BaseModel):
    """Single data issue detected during validation."""

    model_config = ConfigDict(frozen=True)

    issue_type: DataIssueType
    severity: IssueSeverity
    trade_date: date | None = None
    description: str


class DataIntegrityReport(BaseModel):
    """Comprehensive integrity report for a market data time series."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    total_bars: int
    is_valid: bool = Field(description="True if zero ERROR-level issues were detected")
    issues: list[DataIntegrityIssue] = Field(default_factory=list)

    @property
    def error_count(self) -> int:
        """Count of blocking ERROR issues."""
        return sum(1 for issue in self.issues if issue.severity == IssueSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        """Count of non-blocking WARNING issues."""
        return sum(1 for issue in self.issues if issue.severity == IssueSeverity.WARNING)


def validate_market_data_series(
    bars: Sequence[MarketBarRecord],
    check_gaps: bool = False,
    volume_spike_multiplier: float = 20.0,
    calendar: BaseExchangeCalendar | None = None,
) -> DataIntegrityReport:
    """Validate a sequence of daily EOD bars for a single symbol.

    Performs comprehensive checks:
    1. Chronological order & duplicate trade_date detection.
    2. Non-positive price check (<= 0).
    3. OHLC boundary consistency (high >= open/close/low, low <= open/close).
    4. Negative volume & turnover consistency.
    5. Statistical volume anomaly spike check.
    6. Missing weekday trading bars (if check_gaps=True).

    Args:
        bars: Sequence of MarketBarRecord items.
        check_gaps: Whether to flag missing business days between first and last bar.
        volume_spike_multiplier: Multiplier over moving average to flag as volume anomaly.

    Returns:
        DataIntegrityReport with detected issues and overall validity.
    """
    if not bars:
        return DataIntegrityReport(
            symbol="UNKNOWN",
            total_bars=0,
            is_valid=True,
            issues=[],
        )

    symbol = bars[0].symbol
    issues: list[DataIntegrityIssue] = []
    seen_dates: set[date] = set()

    # Track rolling volumes for spike anomaly detection
    recent_volumes: list[int] = []

    for index, bar in enumerate(bars):
        # 0. Symbol consistency across the series
        if bar.symbol != symbol:
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.INCONSISTENT_OHLC,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=(
                        f"Mixed symbols in single series: expected '{symbol}', got '{bar.symbol}'"
                    ),
                )
            )

        # 1. Duplicate check
        if bar.trade_date in seen_dates:
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.DUPLICATE_BAR,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=f"Duplicate bar detected for date {bar.trade_date}",
                )
            )
        else:
            seen_dates.add(bar.trade_date)

        # 1.1 Non-trading day / holiday check if calendar is provided
        if calendar is not None and not calendar.is_trading_day(bar.trade_date):
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.UNEXPECTED_HOLIDAY_BAR,
                    severity=IssueSeverity.WARNING,
                    trade_date=bar.trade_date,
                    description=(f"Bar received on non-trading day/holiday ({bar.trade_date})"),
                )
            )

        # 2. Chronological order check
        if index > 0 and bar.trade_date < bars[index - 1].trade_date:
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.OUT_OF_ORDER,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=(
                        f"Bar date {bar.trade_date} is before previous bar date "
                        f"{bars[index - 1].trade_date}"
                    ),
                )
            )

        # 3. Non-positive price check
        for price_name, val in [
            ("raw_open", bar.raw_open),
            ("raw_high", bar.raw_high),
            ("raw_low", bar.raw_low),
            ("raw_close", bar.raw_close),
            ("adj_open", bar.adj_open),
            ("adj_high", bar.adj_high),
            ("adj_low", bar.adj_low),
            ("adj_close", bar.adj_close),
        ]:
            if val <= Decimal("0"):
                issues.append(
                    DataIntegrityIssue(
                        issue_type=DataIssueType.NON_POSITIVE_PRICE,
                        severity=IssueSeverity.ERROR,
                        trade_date=bar.trade_date,
                        description=f"{price_name} ({val}) is non-positive",
                    )
                )

        # 4. Inconsistent High / Low
        if bar.raw_high < bar.raw_low or bar.adj_high < bar.adj_low:
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.INCONSISTENT_OHLC,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=f"High price is lower than low price on {bar.trade_date}",
                )
            )

        if bar.raw_high < max(bar.raw_open, bar.raw_close) or bar.raw_low > min(
            bar.raw_open, bar.raw_close
        ):
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.INCONSISTENT_OHLC,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=f"Raw OHLC boundaries violated on {bar.trade_date}",
                )
            )

        # 5. Volume and turnover checks
        if bar.volume < 0:
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.NEGATIVE_VOLUME,
                    severity=IssueSeverity.ERROR,
                    trade_date=bar.trade_date,
                    description=f"Negative volume ({bar.volume}) on {bar.trade_date}",
                )
            )

        if bar.volume > 0 and bar.turnover_try <= Decimal("0"):
            issues.append(
                DataIntegrityIssue(
                    issue_type=DataIssueType.VOLUME_TURNOVER_MISMATCH,
                    severity=IssueSeverity.WARNING,
                    trade_date=bar.trade_date,
                    description=(
                        f"Non-zero volume ({bar.volume}) with zero/negative turnover "
                        f"({bar.turnover_try}) on {bar.trade_date}"
                    ),
                )
            )

        # 6. Volume spike anomaly check against rolling history
        if len(recent_volumes) >= 5 and bar.volume > 0:
            avg_vol = sum(recent_volumes) / len(recent_volumes)
            if avg_vol > 0 and bar.volume > avg_vol * volume_spike_multiplier:
                issues.append(
                    DataIntegrityIssue(
                        issue_type=DataIssueType.VOLUME_SPIKE_ANOMALY,
                        severity=IssueSeverity.WARNING,
                        trade_date=bar.trade_date,
                        description=(
                            f"Volume spike ({bar.volume}) exceeds {volume_spike_multiplier}x "
                            f"rolling average ({avg_vol:.0f}) on {bar.trade_date}"
                        ),
                    )
                )

        recent_volumes.append(bar.volume)
        if len(recent_volumes) > 20:
            recent_volumes.pop(0)

    # 7. Check for gaps between trading days (if requested)
    if check_gaps and len(bars) > 1:
        current_date = bars[0].trade_date
        end_date = bars[-1].trade_date
        while current_date < end_date:
            current_date += timedelta(days=1)
            is_expected_day = (
                calendar.is_trading_day(current_date)
                if calendar is not None
                else current_date.weekday() < 5
            )
            if is_expected_day and current_date not in seen_dates:
                issues.append(
                    DataIntegrityIssue(
                        issue_type=DataIssueType.MISSING_BAR,
                        severity=IssueSeverity.WARNING,
                        trade_date=current_date,
                        description=f"Missing trading bar on {current_date}",
                    )
                )

    has_blocking_errors = any(issue.severity == IssueSeverity.ERROR for issue in issues)

    return DataIntegrityReport(
        symbol=symbol,
        total_bars=len(bars),
        is_valid=not has_blocking_errors,
        issues=issues,
    )
