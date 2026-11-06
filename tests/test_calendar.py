"""Tests for exchange calendar, trading sessions, holidays, and trading halt tracking."""

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from stock_analysis.pipeline.calendar import (
    BISTCalendar,
    HaltReason,
    TradingHalt,
)
from stock_analysis.pipeline.validator import (
    DataIssueType,
    IssueSeverity,
    validate_market_data_series,
)
from stock_analysis.providers.base import DataStatus, MarketBarRecord


def _make_bar(
    trade_date: date,
    symbol: str = "THYAO",
    close: str = "100.00",
    volume: int = 1000,
) -> MarketBarRecord:
    c = Decimal(close)
    dt_utc = datetime(
        trade_date.year, trade_date.month, trade_date.day, 15, 10, tzinfo=ZoneInfo("UTC")
    )
    return MarketBarRecord(
        symbol=symbol,
        exchange="BIST",
        trade_date=trade_date,
        session_time_utc=dt_utc,
        raw_open=c,
        raw_high=c + Decimal("2.00"),
        raw_low=c - Decimal("2.00"),
        raw_close=c,
        adj_open=c,
        adj_high=c + Decimal("2.00"),
        adj_low=c - Decimal("2.00"),
        adj_close=c,
        volume=volume,
        turnover_try=c * Decimal(str(volume)),
        currency="TRY",
        data_status=DataStatus.VERIFIED,
        source_name="test",
    )


def test_bist_calendar_timezone() -> None:
    calendar = BISTCalendar()
    assert calendar.timezone == ZoneInfo("Europe/Istanbul")


def test_bist_calendar_weekends() -> None:
    calendar = BISTCalendar()
    # 2024-10-19 is Saturday, 2024-10-20 is Sunday
    assert not calendar.is_trading_day(date(2024, 10, 19))
    assert not calendar.is_trading_day(date(2024, 10, 20))
    # 2024-10-21 is Monday (normal trading day)
    assert calendar.is_trading_day(date(2024, 10, 21))


def test_bist_fixed_holidays() -> None:
    calendar = BISTCalendar()
    # Fixed public holidays
    assert not calendar.is_trading_day(date(2024, 1, 1))  # Yılbaşı
    assert not calendar.is_trading_day(date(2024, 4, 23))  # 23 Nisan
    assert not calendar.is_trading_day(date(2024, 5, 1))  # 1 Mayıs
    assert not calendar.is_trading_day(date(2024, 5, 19))  # 19 Mayıs
    assert not calendar.is_trading_day(date(2024, 7, 15))  # 15 Temmuz
    assert not calendar.is_trading_day(date(2024, 8, 30))  # 30 Ağustos
    assert not calendar.is_trading_day(date(2024, 10, 29))  # 29 Ekim


def test_bist_variable_holidays() -> None:
    calendar = BISTCalendar()
    # 2024 Ramazan & Kurban
    assert not calendar.is_trading_day(date(2024, 4, 10))
    assert not calendar.is_trading_day(date(2024, 6, 17))
    # 2025 Ramazan & Kurban
    assert not calendar.is_trading_day(date(2025, 3, 31))
    assert not calendar.is_trading_day(date(2025, 6, 6))
    # 2026 Ramazan & Kurban
    assert not calendar.is_trading_day(date(2026, 3, 20))
    assert not calendar.is_trading_day(date(2026, 5, 27))


def test_bist_half_days_and_sessions() -> None:
    calendar = BISTCalendar()
    # 28 Ekim is half-day
    oct_28 = date(2024, 10, 28)
    assert calendar.is_trading_day(oct_28)
    assert calendar.is_half_day(oct_28)
    assert calendar.get_session_hours(oct_28) == (time(10, 0), time(12, 40))

    # Ramazan arifesi 2024-04-09 is half-day
    apr_9 = date(2024, 4, 9)
    assert calendar.is_trading_day(apr_9)
    assert calendar.is_half_day(apr_9)
    assert calendar.get_session_hours(apr_9) == (time(10, 0), time(12, 40))

    # Normal day
    oct_25 = date(2024, 10, 25)
    assert calendar.is_trading_day(oct_25)
    assert not calendar.is_half_day(oct_25)
    assert calendar.get_session_hours(oct_25) == (time(10, 0), time(18, 10))

    # Closed day (29 Ekim)
    oct_29 = date(2024, 10, 29)
    assert calendar.get_session_hours(oct_29) is None


def test_is_session_open_at() -> None:
    calendar = BISTCalendar()
    # Monday 2024-10-21, 14:00 TRT is 11:00 UTC
    open_ts = datetime(2024, 10, 21, 11, 0, tzinfo=ZoneInfo("UTC"))
    assert calendar.is_session_open_at(open_ts)

    # Monday 2024-10-21, 09:30 TRT (06:30 UTC) - pre-market closed
    early_ts = datetime(2024, 10, 21, 6, 30, tzinfo=ZoneInfo("UTC"))
    assert not calendar.is_session_open_at(early_ts)

    # Monday 2024-10-21, 19:00 TRT (16:00 UTC) - post-market closed
    late_ts = datetime(2024, 10, 21, 16, 0, tzinfo=ZoneInfo("UTC"))
    assert not calendar.is_session_open_at(late_ts)

    # 28 Ekim half day: 11:00 TRT (08:00 UTC) -> open
    half_open = datetime(2024, 10, 28, 8, 0, tzinfo=ZoneInfo("UTC"))
    assert calendar.is_session_open_at(half_open)

    # 28 Ekim half day: 14:00 TRT (11:00 UTC) -> closed after 12:40
    half_closed = datetime(2024, 10, 28, 11, 0, tzinfo=ZoneInfo("UTC"))
    assert not calendar.is_session_open_at(half_closed)


def test_trading_halt_registry() -> None:
    calendar = BISTCalendar()
    halt_start = datetime(2024, 10, 21, 11, 0, tzinfo=ZoneInfo("UTC"))
    halt_end = datetime(2024, 10, 21, 11, 30, tzinfo=ZoneInfo("UTC"))

    # Register single symbol halt (VBTS / Devre Kesici)
    thyao_halt = TradingHalt(
        symbol="THYAO",
        exchange="BIST",
        halt_start_utc=halt_start,
        halt_end_utc=halt_end,
        reason=HaltReason.CIRCUIT_BREAKER_SYMBOL,
        description="Tek Pay Devre Kesici tetiklendi",
    )
    calendar.register_halt(thyao_halt)

    check_ts = datetime(2024, 10, 21, 11, 15, tzinfo=ZoneInfo("UTC"))
    assert calendar.is_halted(check_ts, symbol="THYAO")
    assert not calendar.is_halted(check_ts, symbol="GARAN")

    # After halt ends
    after_ts = datetime(2024, 10, 21, 11, 35, tzinfo=ZoneInfo("UTC"))
    assert not calendar.is_halted(after_ts, symbol="THYAO")

    # Register market-wide halt (E-DKS)
    market_halt = TradingHalt(
        symbol=None,
        exchange="BIST",
        halt_start_utc=halt_start,
        halt_end_utc=None,  # Open-ended until resolved
        reason=HaltReason.CIRCUIT_BREAKER_INDEX,
        description="Endeks Devre Kesici tetiklendi",
    )
    calendar.register_halt(market_halt)
    # Market halt affects any symbol
    assert calendar.is_halted(check_ts, symbol="GARAN")
    assert calendar.is_halted(check_ts, symbol="THYAO")


def test_validator_with_calendar_holiday_bar() -> None:
    calendar = BISTCalendar()
    # Bar recorded on a public holiday (23 Nisan 2024)
    holiday_bar = _make_bar(date(2024, 4, 23))
    report = validate_market_data_series([holiday_bar], calendar=calendar)

    assert report.is_valid  # UNEXPECTED_HOLIDAY_BAR is a warning, not a blocking error
    assert report.warning_count == 1
    issue = report.issues[0]
    assert issue.issue_type == DataIssueType.UNEXPECTED_HOLIDAY_BAR
    assert issue.severity == IssueSeverity.WARNING
    assert issue.trade_date == date(2024, 4, 23)


def test_validator_gap_check_with_and_without_calendar() -> None:
    calendar = BISTCalendar()
    # 2024-04-22 (Monday), 2024-04-23 (Tuesday - 23 Nisan Tatili), 2024-04-24 (Wednesday)
    bars = [
        _make_bar(date(2024, 4, 22)),
        _make_bar(date(2024, 4, 24)),
    ]

    # Without calendar: Tuesday 23 Nisan is considered a missing trading bar
    report_no_cal = validate_market_data_series(bars, check_gaps=True, calendar=None)
    missing_issues = [i for i in report_no_cal.issues if i.issue_type == DataIssueType.MISSING_BAR]
    assert len(missing_issues) == 1
    assert missing_issues[0].trade_date == date(2024, 4, 23)

    # With calendar: 23 Nisan is recognized as holiday, so NO missing bar is flagged
    report_cal = validate_market_data_series(bars, check_gaps=True, calendar=calendar)
    cal_missing = [i for i in report_cal.issues if i.issue_type == DataIssueType.MISSING_BAR]
    assert len(cal_missing) == 0
