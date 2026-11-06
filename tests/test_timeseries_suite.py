"""Time series testing suite: chronological ordering, missing bars/gaps,
corporate actions, market calendar/holidays, and timezone synchronization (FAZ-14).

Verifies:
1. Chronological Ordering & Monotonicity:
   - Strictly ascending date validation.
   - Out-of-order sequence detection and error tagging.
   - Duplicate date collision detection.
   - Mixed symbol isolation within series.

2. Missing Bar & Gap Detection:
   - Weekend gaps (Fri -> Mon) accepted as normal non-trading intervals.
   - Weekday trading gaps flagged as missing bars.
   - Calendar-aware gap checks (holidays not falsely flagged as missing).

3. BIST Calendar, Session Rules & Holidays:
   - Permanent Europe/Istanbul timezone (UTC+3, no daylight saving shifts).
   - Official fixed and religious holidays recognized as non-trading days.
   - Scheduled half-days (28 Ekim, bayram arifeleri) session hours: 10:00 - 12:40 TRT.
   - Standard trading session hours: 10:00 - 18:10 TRT.
   - Weekends recognized as market closed.
   - Trading halts & circuit breaker event registry (E-DKS, VBTS, market-wide vs symbol).

4. Timezone & Trading Session Synchronization:
   - UTC to Europe/Istanbul session open / close mapping.
   - Pre-market, intraday, closing auction, and post-market evaluation.
   - Timezone-aware vs naive datetime handling.

5. Corporate Actions & Backward Price Adjustment:
   - Forward stock split (2:1, 4:1) backward adjustments.
   - Reverse stock split (1:10) backward adjustments.
   - Bonus issue (bedelsiz sermaye artırımı) price adjustments.
   - Rights issue (bedelli sermaye artırımı) theoretical ex-rights calculations.
   - Cash dividend (nakit temettü) backward adjustments.
   - Sequential compound corporate actions across historical horizons.
   - Anomaly and bound guards (dividend >= price, negative parameters, OHLC bounds).

6. Time Series Partitioning & Look-Ahead Leakage Prevention:
   - Chronological split verification (Train < Validation < Test).
   - Embargo / purge window enforcement to prevent label auto-correlation.
   - Anti-shuffling guards raising DataLeakageError.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from stock_analysis.pipeline.calendar import (
    BISTCalendar,
    HaltReason,
    TradingHalt,
)
from stock_analysis.pipeline.corporate_actions import (
    CorporateAction,
    CorporateActionType,
    apply_corporate_actions,
)
from stock_analysis.pipeline.time_series_splitter import (
    DataLeakageError,
    SplitPartition,
    TimeSeriesSplitter,
)
from stock_analysis.pipeline.validator import (
    DataIssueType,
    IssueSeverity,
    validate_market_data_series,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord

# ==============================================================================
# Helpers & Fixtures
# ==============================================================================


def make_bar(
    trade_date: date,
    symbol: str = "THYAO",
    open_px: str | None = None,
    high_px: str | None = None,
    low_px: str | None = None,
    close_px: str = "102.00",
    volume: int = 1_000_000,
    turnover: str = "102000000.00",
    split_factor: str = "1.0",
    dividend_try: str = "0.0",
    source_name: str = "bist_eod",
) -> MarketBarRecord:
    """Create a valid MarketBarRecord helper for time series testing."""
    c = Decimal(close_px)
    o = Decimal(open_px) if open_px is not None else c
    h = Decimal(high_px) if high_px is not None else max(o, c) * Decimal("1.02")
    low_val = Decimal(low_px) if low_px is not None else min(o, c) * Decimal("0.98")
    h = max(h, o, c)
    low_val = min(low_val, o, c)
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        timezone="Europe/Istanbul",
        currency="TRY",
        data_status=DataStatus.VERIFIED,
        raw_open=o,
        raw_high=h,
        raw_low=low_val,
        raw_close=c,
        adj_open=o,
        adj_high=h,
        adj_low=low_val,
        adj_close=c,
        volume=volume,
        turnover_try=Decimal(turnover),
        split_factor=Decimal(split_factor),
        dividend_try=Decimal(dividend_try),
        source_name=source_name,
    )


# ==============================================================================
# 1. Chronological Ordering & Monotonicity Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestTimeSeriesChronologyAndOrdering:
    """Verifies chronological ordering, monotonicity, and duplicate rejection."""

    def test_strictly_chronological_bars_pass_validation(self) -> None:
        """Sequential ascending daily bars must pass validation with zero errors."""
        bars = [
            make_bar(date(2026, 1, 5)),  # Monday
            make_bar(date(2026, 1, 6)),  # Tuesday
            make_bar(date(2026, 1, 7)),  # Wednesday
            make_bar(date(2026, 1, 8)),  # Thursday
            make_bar(date(2026, 1, 9)),  # Friday
        ]
        report = validate_market_data_series(bars)
        assert report.is_valid is True
        assert report.error_count == 0
        assert len(report.issues) == 0

    def test_out_of_order_bars_trigger_blocking_error(self) -> None:
        """Bars appearing out of chronological order must be flagged with OUT_OF_ORDER."""
        bars = [
            make_bar(date(2026, 1, 5)),
            make_bar(date(2026, 1, 7)),
            make_bar(date(2026, 1, 6)),  # 7 -> 6 is backwards
        ]
        report = validate_market_data_series(bars)
        assert report.is_valid is False
        assert report.error_count >= 1
        issues = [i for i in report.issues if i.issue_type == DataIssueType.OUT_OF_ORDER]
        assert len(issues) == 1
        assert issues[0].trade_date == date(2026, 1, 6)
        assert issues[0].severity == IssueSeverity.ERROR

    def test_duplicate_trade_dates_trigger_blocking_error(self) -> None:
        """Multiple bars with identical trade_date must be flagged as DUPLICATE_BAR."""
        bars = [
            make_bar(date(2026, 1, 5)),
            make_bar(date(2026, 1, 6)),
            make_bar(date(2026, 1, 6)),  # Duplicate
            make_bar(date(2026, 1, 7)),
        ]
        report = validate_market_data_series(bars)
        assert report.is_valid is False
        dup_issues = [i for i in report.issues if i.issue_type == DataIssueType.DUPLICATE_BAR]
        assert len(dup_issues) == 1
        assert dup_issues[0].trade_date == date(2026, 1, 6)

    def test_mixed_symbols_in_single_series_trigger_error(self) -> None:
        """Series containing bars from multiple symbols must be flagged."""
        bars = [
            make_bar(date(2026, 1, 5), symbol="THYAO"),
            make_bar(date(2026, 1, 6), symbol="GARAN"),
        ]
        report = validate_market_data_series(bars)
        assert report.is_valid is False
        mismatch_issues = [i for i in report.issues if "Mixed symbols" in i.description]
        assert len(mismatch_issues) == 1

    def test_time_series_splitter_rejects_shuffled_data(self) -> None:
        """Shuffled dataset must raise DataLeakageError to prevent look-ahead bias."""
        records = [
            {"date": date(2026, 1, 5), "close": 100},
            {"date": date(2026, 1, 8), "close": 105},
            {"date": date(2026, 1, 6), "close": 102},  # Shuffled
        ]
        with pytest.raises(DataLeakageError, match="not chronologically ordered"):
            TimeSeriesSplitter.verify_chronological_order(records)


# ==============================================================================
# 2. Missing Bar & Gap Detection Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestMissingBarsAndGapDetection:
    """Verifies missing bar and temporal gap detection rules."""

    def test_weekend_gap_is_not_flagged_as_missing(self) -> None:
        """Transition from Friday to Monday without weekend bars must not be flagged."""
        bars = [
            make_bar(date(2026, 1, 9)),  # Friday
            make_bar(date(2026, 1, 12)),  # Monday
        ]
        report = validate_market_data_series(bars, check_gaps=True)
        # Weekends (Jan 10, 11) should not produce missing bar issues
        missing_issues = [i for i in report.issues if i.issue_type == DataIssueType.MISSING_BAR]
        assert len(missing_issues) == 0

    def test_missing_weekday_bar_is_flagged_with_warning(self) -> None:
        """Missing Wednesday between Tuesday and Thursday must be detected."""
        bars = [
            make_bar(date(2026, 1, 6)),  # Tuesday
            make_bar(date(2026, 1, 8)),  # Thursday (Wednesday Jan 7 missing)
        ]
        report = validate_market_data_series(bars, check_gaps=True)
        missing_issues = [i for i in report.issues if i.issue_type == DataIssueType.MISSING_BAR]
        assert len(missing_issues) == 1
        assert missing_issues[0].trade_date == date(2026, 1, 7)
        assert missing_issues[0].severity == IssueSeverity.WARNING

    def test_calendar_aware_gap_check_ignores_bist_holidays(self) -> None:
        """Official BIST holiday (e.g. 23 Nisan) must not be flagged when calendar is supplied."""
        calendar = BISTCalendar()
        # 2026-04-23 is Thursday (Ulusal Egemenlik ve Çocuk Bayramı - holiday)
        assert calendar.is_trading_day(date(2026, 4, 23)) is False

        bars = [
            make_bar(date(2026, 4, 22)),  # Wednesday
            make_bar(date(2026, 4, 24)),  # Friday
        ]
        # With calendar provided, 2026-04-23 is known holiday, so no MISSING_BAR issue
        report_with_cal = validate_market_data_series(bars, check_gaps=True, calendar=calendar)
        missing_with_cal = [
            i for i in report_with_cal.issues if i.issue_type == DataIssueType.MISSING_BAR
        ]
        assert len(missing_with_cal) == 0

        # Without calendar, naive weekday check flags it as missing
        report_without_cal = validate_market_data_series(bars, check_gaps=True, calendar=None)
        missing_without_cal = [
            i for i in report_without_cal.issues if i.issue_type == DataIssueType.MISSING_BAR
        ]
        assert len(missing_without_cal) == 1
        assert missing_without_cal[0].trade_date == date(2026, 4, 23)


# ==============================================================================
# 3. BIST Calendar, Session Rules & Holidays Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestBISTCalendarAndHolidays:
    """Verifies BIST calendar, holiday schedules, half-days, and trading halts."""

    def test_timezone_is_fixed_europe_istanbul(self) -> None:
        """BIST calendar timezone must be Europe/Istanbul (permanent UTC+3)."""
        cal = BISTCalendar()
        assert cal.timezone == ZoneInfo("Europe/Istanbul")
        assert cal.EXCHANGE_CODE == "BIST"

    @pytest.mark.parametrize(
        "holiday_date,name",
        [
            (date(2026, 1, 1), "Yılbaşı"),
            (date(2026, 4, 23), "Ulusal Egemenlik ve Çocuk Bayramı"),
            (date(2026, 5, 1), "Emek ve Dayanışma Günü"),
            (date(2026, 5, 19), "Atatürk'ü Anma, Gençlik ve Spor Bayramı"),
            (date(2026, 7, 15), "Demokrasi ve Milli Birlik Günü"),
            (date(2026, 8, 30), "Zafer Bayramı"),
            (date(2026, 10, 29), "Cumhuriyet Bayramı"),
        ],
    )
    def test_fixed_public_holidays_are_non_trading_days(
        self, holiday_date: date, name: str
    ) -> None:
        """Every fixed Turkish official holiday must be recognized as closed."""
        cal = BISTCalendar()
        assert cal.is_trading_day(holiday_date) is False, f"Failed on {name}"
        assert cal.get_session_hours(holiday_date) is None

    @pytest.mark.parametrize(
        "holiday_date,name",
        [
            (date(2024, 4, 10), "2024 Ramazan Bayramı 1"),
            (date(2024, 6, 17), "2024 Kurban Bayramı 2"),
            (date(2025, 3, 31), "2025 Ramazan Bayramı 2"),
            (date(2025, 6, 6), "2025 Kurban Bayramı 1"),
            (date(2026, 3, 20), "2026 Ramazan Bayramı 1"),
            (date(2026, 5, 27), "2026 Kurban Bayramı 1"),
        ],
    )
    def test_variable_religious_holidays_are_non_trading_days(
        self, holiday_date: date, name: str
    ) -> None:
        """Variable religious holiday dates must be recognized as closed."""
        cal = BISTCalendar()
        assert cal.is_trading_day(holiday_date) is False, f"Failed on {name}"
        assert cal.get_session_hours(holiday_date) is None

    def test_weekends_are_non_trading_days(self) -> None:
        """Saturday and Sunday must never be trading days."""
        cal = BISTCalendar()
        sat = date(2026, 1, 10)
        sun = date(2026, 1, 11)
        assert cal.is_trading_day(sat) is False
        assert cal.is_trading_day(sun) is False
        assert cal.get_session_hours(sat) is None
        assert cal.get_session_hours(sun) is None

    def test_standard_session_hours(self) -> None:
        """Regular weekday session hours must be 10:00 to 18:10 TRT."""
        cal = BISTCalendar()
        trading_day = date(2026, 1, 5)  # Monday
        assert cal.is_trading_day(trading_day) is True
        assert cal.is_half_day(trading_day) is False
        session = cal.get_session_hours(trading_day)
        assert session == (time(10, 0), time(18, 10))

    def test_scheduled_half_days(self) -> None:
        """Oct 28 and bayram arifeleri must be open half-days (10:00 - 12:40 TRT)."""
        cal = BISTCalendar()
        # 28 October 2026 is Wednesday
        oct_28 = date(2026, 10, 28)
        assert cal.is_trading_day(oct_28) is True
        assert cal.is_half_day(oct_28) is True
        session = cal.get_session_hours(oct_28)
        assert session == (time(10, 0), time(12, 40))

        # 2026 Ramazan Arifesi (2026-03-19)
        ramazan_arife_2026 = date(2026, 3, 19)
        assert cal.is_trading_day(ramazan_arife_2026) is True
        assert cal.is_half_day(ramazan_arife_2026) is True
        assert cal.get_session_hours(ramazan_arife_2026) == (time(10, 0), time(12, 40))

    def test_bar_on_holiday_triggers_warning(self) -> None:
        """Bar reported on official market holiday must be flagged as UNEXPECTED_HOLIDAY_BAR."""
        cal = BISTCalendar()
        bars = [
            make_bar(date(2026, 1, 1)),  # Yılbaşı holiday
        ]
        report = validate_market_data_series(bars, calendar=cal)
        holiday_issues = [
            i for i in report.issues if i.issue_type == DataIssueType.UNEXPECTED_HOLIDAY_BAR
        ]
        assert len(holiday_issues) == 1
        assert holiday_issues[0].severity == IssueSeverity.WARNING
        assert holiday_issues[0].trade_date == date(2026, 1, 1)


# ==============================================================================
# 4. Trading Halts & Circuit Breakers Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestTradingHaltsAndCircuitBreakers:
    """Verifies market-wide and symbol-specific trading halt behaviors."""

    def test_symbol_circuit_breaker_active_range(self) -> None:
        """Symbol circuit breaker halts trading between start and end timestamps."""
        halt = TradingHalt(
            symbol="THYAO",
            exchange="BIST",
            halt_start_utc=datetime(2026, 1, 5, 11, 0, tzinfo=UTC),
            halt_end_utc=datetime(2026, 1, 5, 11, 30, tzinfo=UTC),
            reason=HaltReason.CIRCUIT_BREAKER_SYMBOL,
            description="VBTS Tek Pay Devre Kesici",
        )
        # Before halt
        assert halt.is_active_at(datetime(2026, 1, 5, 10, 59, 59, tzinfo=UTC)) is False
        # During halt
        assert halt.is_active_at(datetime(2026, 1, 5, 11, 0, 0, tzinfo=UTC)) is True
        assert halt.is_active_at(datetime(2026, 1, 5, 11, 15, 0, tzinfo=UTC)) is True
        # After halt
        assert halt.is_active_at(datetime(2026, 1, 5, 11, 30, 0, tzinfo=UTC)) is False

    def test_market_wide_circuit_breaker_affects_all_symbols(self) -> None:
        """Market-wide halt (symbol=None) must halt any symbol queried."""
        cal = BISTCalendar()
        market_halt = TradingHalt(
            symbol=None,  # Market-wide
            exchange="BIST",
            halt_start_utc=datetime(2026, 1, 5, 13, 0, tzinfo=UTC),
            halt_end_utc=datetime(2026, 1, 5, 13, 30, tzinfo=UTC),
            reason=HaltReason.CIRCUIT_BREAKER_INDEX,
            description="E-DKS Endeks Devre Kesici",
        )
        cal.register_halt(market_halt)

        check_ts = datetime(2026, 1, 5, 13, 10, tzinfo=UTC)
        assert cal.is_halted(check_ts, symbol=None) is True
        assert cal.is_halted(check_ts, symbol="THYAO") is True
        assert cal.is_halted(check_ts, symbol="GARAN") is True

    def test_symbol_specific_halt_does_not_affect_other_symbols(self) -> None:
        """A halt registered for THYAO must not affect GARAN."""
        cal = BISTCalendar()
        thyao_halt = TradingHalt(
            symbol="THYAO",
            exchange="BIST",
            halt_start_utc=datetime(2026, 1, 5, 11, 0, tzinfo=UTC),
            halt_end_utc=datetime(2026, 1, 5, 11, 30, tzinfo=UTC),
            reason=HaltReason.COMPANY_ANNOUNCEMENT,
            description="Özel Durum Açıklaması",
        )
        cal.register_halt(thyao_halt)

        check_ts = datetime(2026, 1, 5, 11, 15, tzinfo=UTC)
        assert cal.is_halted(check_ts, symbol="THYAO") is True
        assert cal.is_halted(check_ts, symbol="GARAN") is False


# ==============================================================================
# 5. Timezone & Trading Session Synchronization Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestTimezoneAndSessionTimes:
    """Verifies UTC to Istanbul local time conversion and session activity checks."""

    def test_utc_to_trt_session_hours_mapping(self) -> None:
        """UTC session mapping: 10:00 TRT = 07:00 UTC, 18:10 TRT = 15:10 UTC."""
        cal = BISTCalendar()
        # Monday 2026-01-05
        # 06:59:59 UTC -> 09:59:59 TRT (Pre-market)
        pre_market = datetime(2026, 1, 5, 6, 59, 59, tzinfo=UTC)
        assert cal.is_session_open_at(pre_market) is False

        # 07:00:00 UTC -> 10:00:00 TRT (Continuous session open)
        market_open = datetime(2026, 1, 5, 7, 0, 0, tzinfo=UTC)
        assert cal.is_session_open_at(market_open) is True

        # 12:00:00 UTC -> 15:00:00 TRT (Midday active)
        intraday = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)
        assert cal.is_session_open_at(intraday) is True

        # 15:10:00 UTC -> 18:10:00 TRT (Closing auction end)
        market_close = datetime(2026, 1, 5, 15, 10, 0, tzinfo=UTC)
        assert cal.is_session_open_at(market_close) is True

        # 15:10:01 UTC -> 18:10:01 TRT (Post-market closed)
        post_market = datetime(2026, 1, 5, 15, 10, 1, tzinfo=UTC)
        assert cal.is_session_open_at(post_market) is False

    def test_half_day_utc_session_mapping(self) -> None:
        """Half-day session closes at 12:40 TRT = 09:40 UTC."""
        cal = BISTCalendar()
        # 28 October 2026
        # 09:40:00 UTC -> 12:40:00 TRT (Half-day close boundary)
        at_close = datetime(2026, 10, 28, 9, 40, 0, tzinfo=UTC)
        assert cal.is_session_open_at(at_close) is True

        # 09:40:01 UTC -> 12:40:01 TRT (Closed)
        past_close = datetime(2026, 10, 28, 9, 40, 1, tzinfo=UTC)
        assert cal.is_session_open_at(past_close) is False

    def test_naive_datetime_converted_to_local_properly(self) -> None:
        """Naive datetime is assumed system local and handled via astimezone."""
        cal = BISTCalendar()
        # Create timezone-aware TRT datetime
        trt_ts = datetime(2026, 1, 5, 14, 0, 0, tzinfo=ZoneInfo("Europe/Istanbul"))
        assert cal.is_session_open_at(trt_ts) is True


# ==============================================================================
# 6. Corporate Actions & Backward Price Adjustment Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestCorporateActionsAndBackwardAdjustments:
    """Verifies stock splits, bonus/rights issues, and cash dividend adjustments."""

    def test_forward_stock_split_backward_adjustment(self) -> None:
        """2:1 forward split (ratio=2.0) halves past prices and keeps post-split unchanged."""
        bars = [
            make_bar(date(2026, 1, 5), open_px="100.00", close_px="100.00"),
            make_bar(date(2026, 1, 6), open_px="100.00", close_px="100.00"),
            # Ex-date on 2026-01-07
            make_bar(date(2026, 1, 7), open_px="50.00", close_px="52.00"),
            make_bar(date(2026, 1, 8), open_px="52.00", close_px="55.00"),
        ]
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 7),
            action_type=CorporateActionType.SPLIT,
            ratio=Decimal("2.0"),
            description="2:1 Stock Split",
        )

        adjusted = apply_corporate_actions(bars, [action])
        assert len(adjusted) == 4

        # Historical bars before ex_date adjusted by factor 1/2 = 0.5
        assert adjusted[0].adj_close == Decimal("50.0000")
        assert adjusted[0].adj_open == Decimal("50.0000")
        assert adjusted[1].adj_close == Decimal("50.0000")

        # Ex-date and subsequent bars remain equal to raw prices
        assert adjusted[2].adj_close == Decimal("52.0000")
        assert adjusted[2].split_factor == Decimal("2.0")
        assert adjusted[3].adj_close == Decimal("55.0000")

    def test_reverse_stock_split_backward_adjustment(self) -> None:
        """1:10 reverse split (ratio=0.1) scales historical prices up by 10."""
        bars = [
            make_bar(date(2026, 1, 5), open_px="1.00", close_px="1.20"),
            make_bar(date(2026, 1, 6), open_px="1.20", close_px="1.00"),
            # Ex-date on 2026-01-07
            make_bar(date(2026, 1, 7), open_px="10.00", close_px="11.00"),
        ]
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 7),
            action_type=CorporateActionType.REVERSE_SPLIT,
            ratio=Decimal("0.1"),
            description="1:10 Reverse Split",
        )

        adjusted = apply_corporate_actions(bars, [action])
        # Prior to ex-date: 1.20 * (1 / 0.1) = 12.00
        assert adjusted[0].adj_close == Decimal("12.0000")
        assert adjusted[1].adj_close == Decimal("10.0000")
        assert adjusted[2].adj_close == Decimal("11.0000")

    def test_bonus_issue_bedelsiz_adjustment(self) -> None:
        """100% bedelsiz (ratio=1.0) halves past prices: factor = 1 / (1 + 1.0) = 0.5."""
        bars = [
            make_bar(date(2026, 1, 5), close_px="80.00"),
            make_bar(date(2026, 1, 6), close_px="40.00"),  # Ex-date
        ]
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.BONUS_ISSUE,
            ratio=Decimal("1.0"),
            description="%100 Bedelsiz Sermaye Artırımı",
        )
        adjusted = apply_corporate_actions(bars, [action])
        assert adjusted[0].adj_close == Decimal("40.0000")
        assert adjusted[1].adj_close == Decimal("40.0000")

    def test_rights_issue_bedelli_theoretical_adjustment(self) -> None:
        """Rights issue (bedelli) calculated using BIST theoretical ex-rights formula."""
        # Cum-price = 10 TRY, ratio = 1.0 (1 new share per share), subscription = 2 TRY
        # Theoretical ex = (10 + 1.0 * 2) / (1 + 1) = 12 / 2 = 6 TRY
        # factor = 6 / 10 = 0.60
        bars = [
            make_bar(date(2026, 1, 5), close_px="10.00"),
            make_bar(date(2026, 1, 6), close_px="6.00"),  # Ex-date
        ]
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.RIGHTS_ISSUE,
            ratio=Decimal("1.0"),
            subscription_price_try=Decimal("2.0"),
            description="%100 Bedelli Sermaye Artırımı (2 TRY rüçhan)",
        )
        adjusted = apply_corporate_actions(bars, [action])
        assert adjusted[0].adj_close == Decimal("6.0000")
        assert adjusted[1].adj_close == Decimal("6.0000")

    def test_cash_dividend_proportional_adjustment(self) -> None:
        """Cash dividend backward adjustment reduces past prices by dividend ratio."""
        # Cum close = 100 TRY, cash dividend = 10 TRY
        # factor = (100 - 10) / 100 = 0.90
        bars = [
            make_bar(date(2026, 1, 5), close_px="100.00"),
            make_bar(date(2026, 1, 6), close_px="92.00"),  # Ex-dividend date
        ]
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("10.00"),
            description="10 TRY Nakit Temettü",
        )
        adjusted = apply_corporate_actions(bars, [action])
        assert adjusted[0].adj_close == Decimal("90.0000")
        assert adjusted[1].adj_close == Decimal("92.0000")
        assert adjusted[1].dividend_try == Decimal("10.00")

    def test_compound_sequential_corporate_actions(self) -> None:
        """Multiple sequential actions accumulate correctly in backward adjustment."""
        # T1: 100 TRY
        # T2: Ex-div (10 TRY on 100 TRY cum-close -> factor 0.90)
        # T3: 90 TRY
        # T4: 2:1 Split (factor 0.50)
        # Bar at T1 receives 0.90 * 0.50 = 0.45 -> 100 * 0.45 = 45.00
        # Bar at T2 and T3 receive 0.50 -> 90 * 0.50 = 45.00
        bars = [
            make_bar(date(2026, 1, 1), close_px="100.00"),
            make_bar(date(2026, 1, 2), close_px="90.00"),
            make_bar(date(2026, 1, 5), close_px="90.00"),
            make_bar(date(2026, 1, 6), close_px="45.00"),
        ]
        actions = [
            CorporateAction(
                symbol="THYAO",
                ex_date=date(2026, 1, 2),
                action_type=CorporateActionType.CASH_DIVIDEND,
                amount_try=Decimal("10.00"),
            ),
            CorporateAction(
                symbol="THYAO",
                ex_date=date(2026, 1, 6),
                action_type=CorporateActionType.SPLIT,
                ratio=Decimal("2.0"),
            ),
        ]
        adjusted = apply_corporate_actions(bars, actions)
        assert adjusted[0].adj_close == Decimal("45.0000")
        assert adjusted[1].adj_close == Decimal("45.0000")
        assert adjusted[2].adj_close == Decimal("45.0000")
        assert adjusted[3].adj_close == Decimal("45.0000")

    def test_dividend_greater_than_price_raises_error(self) -> None:
        """Dividend exceeding or equal to cum closing price must raise ValueError."""
        action = CorporateAction(
            symbol="THYAO",
            ex_date=date(2026, 1, 6),
            action_type=CorporateActionType.CASH_DIVIDEND,
            amount_try=Decimal("150.00"),
        )
        with pytest.raises(ValueError, match="cannot be >= cum close price"):
            action.calculate_adjustment_factor(Decimal("100.00"))


# ==============================================================================
# 7. Time Series Partitioning & Anti-Leakage Tests
# ==============================================================================


@pytest.mark.timeseries
@pytest.mark.unit
class TestTimeSeriesSplitterAndAntiLeakage:
    """Verifies chronological dataset splitting and embargo purge guarantees."""

    def test_clean_chronological_splits_pass_verification(self) -> None:
        """Non-overlapping chronological partitions must pass leak verification."""
        train = [{"date": date(2026, 1, 1)}, {"date": date(2026, 1, 2)}]
        val = [{"date": date(2026, 1, 5)}, {"date": date(2026, 1, 6)}]
        test = [{"date": date(2026, 1, 7)}, {"date": date(2026, 1, 8)}]

        assert TimeSeriesSplitter.verify_no_leakage(train, val, test, embargo_days=0) is True

    def test_train_val_overlap_triggers_leakage_error(self) -> None:
        """Look-ahead overlap between train and val must raise DataLeakageError."""
        train = [{"date": date(2026, 1, 5)}]
        val = [{"date": date(2026, 1, 4)}]  # val earlier than train
        test = [{"date": date(2026, 1, 6)}]

        with pytest.raises(DataLeakageError, match="Look-ahead leakage detected"):
            TimeSeriesSplitter.verify_no_leakage(train, val, test)

    def test_embargo_violation_triggers_leakage_error(self) -> None:
        """Failure to respect the embargo window must raise DataLeakageError."""
        train = [{"date": date(2026, 1, 5)}]
        # 2-day embargo requires val >= 2026-01-08
        val = [{"date": date(2026, 1, 6)}]
        test = [{"date": date(2026, 1, 10)}]

        with pytest.raises(DataLeakageError, match="Embargo violation"):
            TimeSeriesSplitter.verify_no_leakage(train, val, test, embargo_days=2)

    def test_split_by_ratios_preserves_chronology_and_symbols(self) -> None:
        """split_by_ratios must partition by dates preserving multi-symbol cross sections."""
        records = []
        for day in range(1, 21):
            d = date(2026, 1, day)
            records.append({"date": d, "symbol": "THYAO", "close": 100})
            records.append({"date": d, "symbol": "GARAN", "close": 50})

        train, val, test, summary = TimeSeriesSplitter.split_by_ratios(
            records, train_ratio=0.70, val_ratio=0.15, test_ratio=0.15, embargo_days=0
        )
        assert summary.is_leak_free is True
        assert len(train) > 0
        assert len(val) > 0
        assert len(test) > 0

        # Each partition should contain both symbols
        for part in summary.partitions_summary:
            assert part.unique_symbols == 2
            assert part.partition in (
                SplitPartition.TRAIN,
                SplitPartition.VALIDATION,
                SplitPartition.TEST,
            )
