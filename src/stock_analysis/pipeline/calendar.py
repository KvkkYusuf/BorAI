"""Exchange calendar, trading sessions, holiday rules, and trading halt tracking."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo


class HaltReason(StrEnum):
    """Reason code for market or symbol trading halt."""

    CIRCUIT_BREAKER_INDEX = "circuit_breaker_index"  # E-DKS (Endeks Devre Kesici)
    CIRCUIT_BREAKER_SYMBOL = "circuit_breaker_symbol"  # VBTS / Tek Pay Devre Kesici
    COMPANY_ANNOUNCEMENT = "company_announcement"  # Özel Durum Açıklaması Beklentisi
    EXTRAORDINARY_EVENT = "extraordinary_event"  # Deprem, mücbir sebep vb.


@dataclass(frozen=True)
class TradingHalt:
    """Record of a temporary or full-day trading halt."""

    symbol: str | None  # None indicates market-wide halt
    exchange: str
    halt_start_utc: datetime
    halt_end_utc: datetime | None
    reason: HaltReason
    description: str = ""

    def is_active_at(self, timestamp_utc: datetime) -> bool:
        """Check whether the halt is active at a specific UTC timestamp."""
        if timestamp_utc < self.halt_start_utc:
            return False
        if self.halt_end_utc is not None and timestamp_utc >= self.halt_end_utc:
            return False
        return True


class BaseExchangeCalendar(ABC):
    """Abstract interface for exchange trading calendars and session hours."""

    @property
    @abstractmethod
    def timezone(self) -> ZoneInfo:
        """Exchange local timezone."""
        ...

    @abstractmethod
    def is_trading_day(self, check_date: date) -> bool:
        """Return True if check_date is an open trading day."""
        ...

    @abstractmethod
    def is_half_day(self, check_date: date) -> bool:
        """Return True if check_date is a scheduled half-day session."""
        ...

    @abstractmethod
    def get_session_hours(self, check_date: date) -> tuple[time, time] | None:
        """Return (open_time, close_time) in exchange local time, or None if market is closed."""
        ...

    def is_session_open_at(self, timestamp: datetime) -> bool:
        """Determine whether the exchange is actively trading at given timestamp."""
        local_ts = timestamp.astimezone(self.timezone)
        session = self.get_session_hours(local_ts.date())
        if session is None:
            return False
        open_time, close_time = session
        return open_time <= local_ts.time() <= close_time


class BISTCalendar(BaseExchangeCalendar):
    """Borsa Istanbul (BIST) Equity Market trading calendar.

    Accounts for:
    - Permanent UTC+3 (Europe/Istanbul) timezone with no daylight saving shifts.
    - Standard trading session: 10:00 - 18:00 TRT (continuous) + 18:00 - 18:10 closing.
    - Half-day sessions (e.g., 28 October, religious holiday eves): 10:00 - 12:40 TRT.
    - Turkish official public and religious holidays.
    - Trading halts and circuit breaker event registry.
    """

    EXCHANGE_CODE = "BIST"
    _TZ = ZoneInfo("Europe/Istanbul")

    # Standard sessions in Istanbul local time
    STANDARD_OPEN = time(10, 0)
    STANDARD_CLOSE = time(18, 10)
    HALF_DAY_CLOSE = time(12, 40)

    # Fixed annual public holidays (month, day)
    FIXED_HOLIDAYS: frozenset[tuple[int, int]] = frozenset(
        {
            (1, 1),  # Yılbaşı
            (4, 23),  # Ulusal Egemenlik ve Çocuk Bayramı
            (5, 1),  # Emek ve Dayanışma Günü
            (5, 19),  # Atatürk'ü Anma, Gençlik ve Spor Bayramı
            (7, 15),  # Demokrasi ve Milli Birlik Günü
            (8, 30),  # Zafer Bayramı
            (10, 29),  # Cumhuriyet Bayramı
        }
    )

    # Variable religious and special holidays (2024 - 2026 calendar cache)
    VARIABLE_HOLIDAYS: frozenset[date] = frozenset(
        {
            # 2024
            date(2024, 4, 10),  # Ramazan Bayramı 1
            date(2024, 4, 11),  # Ramazan Bayramı 2
            date(2024, 4, 12),  # Ramazan Bayramı 3
            date(2024, 6, 17),  # Kurban Bayramı 2 (16 pazar)
            date(2024, 6, 18),  # Kurban Bayramı 3
            date(2024, 6, 19),  # Kurban Bayramı 4
            # 2025
            date(2025, 3, 31),  # Ramazan Bayramı 2 (30 pazar)
            date(2025, 4, 1),  # Ramazan Bayramı 3
            date(2025, 6, 6),  # Kurban Bayramı 1
            date(2025, 6, 9),  # Kurban Bayramı 4
            # 2026
            date(2026, 3, 20),  # Ramazan Bayramı 1
            date(2026, 5, 27),  # Kurban Bayramı 1
            date(2026, 5, 28),  # Kurban Bayramı 2
            date(2026, 5, 29),  # Kurban Bayramı 3
        }
    )

    # Scheduled half-days (e.g. 28 Ekim, religious holiday eves)
    HALF_DAYS: frozenset[tuple[int, int]] = frozenset(
        {
            (10, 28),  # Cumhuriyet Bayramı Arifesi
        }
    )

    VARIABLE_HALF_DAYS: frozenset[date] = frozenset(
        {
            date(2024, 4, 9),  # Ramazan Arifesi
            date(2024, 6, 14),  # Kurban Arifesi
            date(2025, 3, 28),  # Ramazan Arifesi
            date(2025, 6, 5),  # Kurban Arifesi
            date(2026, 3, 19),  # Ramazan Arifesi
            date(2026, 5, 26),  # Kurban Arifesi
        }
    )

    def __init__(self) -> None:
        self._halts: list[TradingHalt] = []

    @property
    def timezone(self) -> ZoneInfo:
        """Exchange local timezone (Europe/Istanbul, UTC+3)."""
        return self._TZ

    def is_trading_day(self, check_date: date) -> bool:
        """Check if check_date is a trading day on BIST."""
        # Weekend check (5=Saturday, 6=Sunday)
        if check_date.weekday() >= 5:
            return False

        # Fixed holidays
        if (check_date.month, check_date.day) in self.FIXED_HOLIDAYS:
            return False

        # Variable holidays
        if check_date in self.VARIABLE_HOLIDAYS:
            return False

        return True

    def is_half_day(self, check_date: date) -> bool:
        """Check if check_date is an open half-day session."""
        if not self.is_trading_day(check_date):
            return False

        if (check_date.month, check_date.day) in self.HALF_DAYS:
            return True

        if check_date in self.VARIABLE_HALF_DAYS:
            return True

        return False

    def get_session_hours(self, check_date: date) -> tuple[time, time] | None:
        """Return (open_time, close_time) in Istanbul local time or None."""
        if not self.is_trading_day(check_date):
            return None

        if self.is_half_day(check_date):
            return (self.STANDARD_OPEN, self.HALF_DAY_CLOSE)

        return (self.STANDARD_OPEN, self.STANDARD_CLOSE)

    def register_halt(self, halt: TradingHalt) -> None:
        """Register a circuit breaker or trading halt event."""
        self._halts.append(halt)

    def get_active_halts(
        self, timestamp_utc: datetime, symbol: str | None = None
    ) -> list[TradingHalt]:
        """Return all active halts at given UTC timestamp matching market or symbol."""
        active: list[TradingHalt] = []
        for h in self._halts:
            if not h.is_active_at(timestamp_utc):
                continue
            # If h.symbol is None, it is a market-wide halt affecting everything
            if h.symbol is None or symbol is None or h.symbol == symbol:
                active.append(h)
        return active

    def is_halted(self, timestamp_utc: datetime, symbol: str | None = None) -> bool:
        """Return True if trading is halted for market or symbol at timestamp."""
        return len(self.get_active_halts(timestamp_utc, symbol)) > 0
